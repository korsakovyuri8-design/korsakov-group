"""Evaluation harness: runs scenarios through the real system.

For each scenario a fresh container is built (in-memory SQLite, the real
packs with the scenario's knowledge/capability edits, an optional scripted
LLM, an optional mocked integration endpoint). Guest steps go through
`Orchestrator.handle()` - the same path as WhatsApp. Staff steps go through
the same `transition_action` the staff API uses.

On top of each scenario's own expectations, every bot message is checked
against the Response Authority invariant: no claim of acceptance/completion
without a stored action in a state that backs it.
"""

from __future__ import annotations

import json
import re
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import select

from app.actions.staff_ops import transition_action
from app.agent.authority import unbacked_claims
from app.clock import FrozenClock
from app.config import Settings
from app.container import Container, build_container
from app.db.models import (
    Action,
    ActionStatus,
    ExternalProvider,
    ExternalTransaction,
    ItineraryItem,
    Guest,
    HumanHandoff,
    Job,
    JobStatus,
    Quote,
    Stay,
    StayStatus,
)
from app.llm.base import ChatMessage, LLMError
from app.observability import configure_logging
from app.schemas.messages import InboundMessage
from app.transactions.callbacks import handle_callback, sign
from app.transactions.providers.mock import MockExternalProvider
from evals.schema import ActionExpect, Scenario, StepExpect

CALLBACK_SECRET = "eval-callback-secret"
CLOCK_START = "2026-10-05T12:00:00+00:00"

ROOT = Path(__file__).resolve().parents[1]
PACKS = {
    "hotel": ROOT / "data" / "hotel" / "example_hotel.yaml",
    "apartment": ROOT / "data" / "properties" / "demo_apartment.yaml",
}
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"


class ScriptedLLM:
    """Simulates model behaviour (including misbehaviour) deterministically."""

    name = "scripted"

    def __init__(self, responses: list[Any]) -> None:
        self.responses = [json.dumps(r) if isinstance(r, dict) else r for r in responses]

    def complete(self, system: str, messages: list[ChatMessage], *, max_tokens: int | None = None,
                 temperature: float = 0.2) -> str:
        if not self.responses:
            raise LLMError("script exhausted")
        item = self.responses.pop(0)
        if item == "ERROR":
            raise LLMError("scripted failure")
        return item


@dataclass
class Turn:
    who: str
    text: str | None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScenarioResult:
    scenario: Scenario
    failures: list[str] = field(default_factory=list)
    transcript: list[Turn] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.failures and self.error is None


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in sorted(directory.glob("*.yaml")):
        for raw in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            scenarios.append(Scenario.model_validate(raw))
    names = [s.name for s in scenarios]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate scenario names: {sorted(dupes)}")
    return scenarios


# ------------------------------------------------------------------ setup
def _pack_data(alias: str) -> dict[str, Any]:
    path = PACKS.get(alias, Path(alias))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _build(scenario: Scenario, workdir: Path) -> tuple[Container, dict[str, str]]:
    paths, slugs = [], {}
    for i, alias in enumerate(scenario.properties):
        data = _pack_data(alias)
        if i == 0:
            if scenario.knowledge:
                data["items"] = [it for it in data["items"] if it["key"] not in scenario.knowledge.remove]
                data["items"].extend(scenario.knowledge.add)
            if scenario.capabilities is not None:
                data["capabilities"] = scenario.capabilities
            for prov in data.get("providers", []):
                prov.setdefault("config", {}).update((scenario.provider_config or {}).get(prov["slug"], {}))
        slug = data["pack"].get("property_slug") or data["pack"]["hotel_slug"]
        slugs[alias] = slug
        path = workdir / f"{i}_{slug}.yaml"
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        paths.append(str(path))

    # Region pack (shared providers) with the same per-scenario overrides.
    region_paths = []
    for i, rp in enumerate(Settings.model_fields["region_pack_paths"].default_factory()):  # type: ignore[misc]
        region = yaml.safe_load((ROOT / rp).read_text(encoding="utf-8"))
        for prov in region.get("providers", []):
            prov.setdefault("config", {}).update((scenario.provider_config or {}).get(prov["slug"], {}))
        target = workdir / f"region_{i}.yaml"
        target.write_text(yaml.safe_dump(region, allow_unicode=True), encoding="utf-8")
        region_paths.append(str(target))

    settings = Settings(
        env="test", database_url="sqlite://", knowledge_path=paths[0], extra_pack_paths=paths[1:],
        region_pack_paths=region_paths,
        hotel_slug=slugs[scenario.properties[0]], log_level="ERROR", staff_api_token="eval",
    )
    integ = scenario.integration

    def handler(request: httpx.Request) -> httpx.Response:
        if integ is None:
            return httpx.Response(503, json={"error": "no integration configured for this scenario"})
        if integ.timeout:
            raise httpx.ReadTimeout("scenario timeout", request=request)
        return httpx.Response(integ.status, json=integ.json_body if integ.json_body is not None else {})

    llm = ScriptedLLM(scenario.llm) if scenario.llm is not None else None
    from datetime import datetime

    container = build_container(settings, llm=llm, http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                                clock=FrozenClock(datetime.fromisoformat(scenario.clock_start or CLOCK_START)))
    return container, slugs


# ------------------------------------------------------------------ checks
def _lower(text: str | None) -> str:
    return (text or "").lower()


def _check_text(prefix: str, text: str | None, contains: list[str], any_of: list[str],
                forbidden: list[str], failures: list[str]) -> None:
    low = _lower(text)
    for c in contains:
        if c.lower() not in low:
            failures.append(f"{prefix}: expected reply to contain {c!r}")
    if any_of and not any(c.lower() in low for c in any_of):
        failures.append(f"{prefix}: expected reply to contain one of {any_of!r}")
    for f in forbidden:
        if f.lower() in low:
            failures.append(f"{prefix}: FORBIDDEN claim {f!r} present")


def _match_actions(prefix: str, expected: list[ActionExpect], actual: list[dict[str, Any]], failures: list[str]) -> None:
    remaining = list(actual)
    for exp in expected:
        found = next((a for a in remaining if a["type"] == exp.type
                      and (exp.status is None or a["status"] == exp.status)
                      and (exp.executor is None or a["executor"] == exp.executor)
                      and (exp.urgency is None or a.get("urgency") == exp.urgency)), None)
        if found is None:
            failures.append(f"{prefix}: expected action {exp.model_dump(exclude_none=True)}; got {actual}")
        else:
            remaining.remove(found)
    if remaining:
        failures.append(f"{prefix}: unexpected actions {remaining}")


def _stay_actions_statuses(container: Container, stay_id: str | None) -> list[ActionStatus]:
    if not stay_id:
        return []
    with container.session_factory() as s:
        return [a.status for a in s.scalars(select(Action).where(Action.stay_id == stay_id))]


def _check_guest_step(prefix: str, exp: StepExpect, reply, container: Container, before_actions: set[str],
                      failures: list[str]) -> None:
    if exp.intent is not None:
        wanted = [exp.intent] if isinstance(exp.intent, str) else exp.intent
        if reply.intent not in wanted:
            failures.append(f"{prefix}: intent {reply.intent} not in {wanted}")
    if exp.language and reply.language != exp.language:
        failures.append(f"{prefix}: language {reply.language} != {exp.language}")
    if exp.grounded is not None and reply.grounded is not exp.grounded:
        failures.append(f"{prefix}: grounded {reply.grounded} != {exp.grounded}")
    if exp.sources is not None:
        missing = [s for s in exp.sources if s not in reply.sources]
        if missing:
            failures.append(f"{prefix}: answer not based on {missing} (sources {reply.sources})")
    if exp.silent is True and reply.text:
        failures.append(f"{prefix}: expected silence, bot said {reply.text!r}")
    if exp.silent is False and not reply.text:
        failures.append(f"{prefix}: expected a reply, bot was silent")
    if exp.outcomes is not None and sorted(exp.outcomes) != sorted(reply.outcomes):
        failures.append(f"{prefix}: outcomes {sorted(reply.outcomes)} != {sorted(exp.outcomes)}")
    for o in exp.outcomes_absent:
        if o in reply.outcomes:
            failures.append(f"{prefix}: outcome {o} present")
    if exp.handoff is not None and reply.handed_off is not exp.handoff:
        failures.append(f"{prefix}: handed_off {reply.handed_off} != {exp.handoff}")
    if exp.handoff_reason:
        reasons = [a.detail.get("reason") for a in reply.actions if a.kind == "handoff"]
        if exp.handoff_reason not in reasons:
            failures.append(f"{prefix}: expected handoff reason {exp.handoff_reason!r}, got {reasons}")
    _check_text(prefix, reply.text, exp.reply_contains, exp.reply_contains_any, exp.forbidden, failures)
    if exp.actions is not None:
        with container.session_factory() as s:
            new = [{"type": a.action_type, "status": a.status.value, "executor": a.executor, "urgency": a.urgency.value}
                   for a in s.scalars(select(Action).order_by(Action.created_at)) if a.id not in before_actions]
        _match_actions(prefix, exp.actions, new, failures)


def _check_transactions(prefix: str, exp: StepExpect, container: Container, before_quotes: set[str],
                        notifications: list[str], failures: list[str], before_txns: set[str] | None = None) -> None:
    with container.session_factory() as s:
        quotes = list(s.scalars(select(Quote).order_by(Quote.created_at.desc())))
        # Prefer quotes created in this step, then open ones (frozen clock => equal timestamps).
        quote = next((q for q in quotes if q.id not in before_quotes and q.status.value == "offered"), None) \
            or next((q for q in quotes if q.id not in before_quotes), None) \
            or next((q for q in quotes if q.status.value == "offered"), None) or (quotes[0] if quotes else None)
        txns = list(s.scalars(select(ExternalTransaction).order_by(ExternalTransaction.created_at.desc())))
        # Prefer a transaction created in this step (frozen clock => equal timestamps).
        txn = next((t for t in txns if before_txns is not None and t.id not in before_txns), None) \
            or (txns[0] if txns else None)
        if exp.quote is not None:
            if quote is None:
                failures.append(f"{prefix}: expected a quote, none exists")
            else:
                actual = {"status": quote.status.value, "amount": f"{quote.amount:.2f}", "currency": quote.currency,
                          **{k: v for k, v in (quote.request or {}).items()}}
                for k, v in exp.quote.items():
                    if k.endswith("_contains"):
                        if str(v) not in str(actual.get(k[:-9])):
                            failures.append(f"{prefix}: quote.{k[:-9]} = {actual.get(k[:-9])!r} lacks {v!r}")
                    elif str(actual.get(k)) != str(v):
                        failures.append(f"{prefix}: quote.{k} = {actual.get(k)!r}, expected {v!r}")
        if exp.no_new_quote and quote is not None and quote.id not in before_quotes \
                and quote.status.value == "offered":
            failures.append(f"{prefix}: a new quote was offered but none was expected")
        if exp.transaction is not None:
            if txn is None:
                failures.append(f"{prefix}: expected a transaction, none exists")
            else:
                actual = {"status": txn.action.status.value, **(txn.request or {})}
                for k, v in exp.transaction.items():
                    if k.endswith("_contains"):
                        if str(v) not in str(actual.get(k[:-9])):
                            failures.append(f"{prefix}: transaction.{k[:-9]} = {actual.get(k[:-9])!r} lacks {v!r}")
                    elif str(actual.get(k)) != str(v):
                        failures.append(f"{prefix}: transaction.{k} = {actual.get(k)!r}, expected {v!r}")
    joined = "\n".join(notifications)
    _check_text(f"{prefix} notifications", joined, exp.notifications_contain, [], exp.notifications_forbidden,
                failures)
    if exp.no_notifications and notifications:
        failures.append(f"{prefix}: expected no notifications, got {notifications}")


def _mock_providers(container: Container) -> list[MockExternalProvider]:
    return [a for a in container.providers_registry.instances() if isinstance(a, MockExternalProvider)]


def _authority_check(prefix: str, text: str | None, container: Container, stay_id: str | None,
                     failures: list[str], *, quoted_sources: list[str] | None = None,
                     property_slug: str | None = None, locale: str = "en") -> None:
    """Check bot/model-generated text. Property-authored knowledge the bot
    quotes verbatim (its own published policy, e.g. "late arrival is confirmed
    only by staff") is authoritative by definition and is excluded."""
    if not text:
        return
    if quoted_sources:
        runtime = container.properties.get(property_slug)
        docs = {d.key: d for d in getattr(runtime.knowledge.retriever, "documents", [])}
        for key in quoted_sources:
            if key in docs:
                text = text.replace(docs[key].text_for(locale), " ")
    claims = unbacked_claims(text, _stay_actions_statuses(container, stay_id))
    if claims:
        failures.append(f"{prefix}: AUTHORITY VIOLATION - claims {sorted(c.value for c in claims)} "
                        f"without backing action state: {text!r}")


def _check_final(scenario: Scenario, container: Container, slugs: dict[str, str], failures: list[str]) -> None:
    f = scenario.final
    with container.session_factory() as s:
        if f.actions is not None:
            actual = [{"type": a.action_type, "status": a.status.value, "executor": a.executor, "urgency": a.urgency.value}
                      for a in s.scalars(select(Action))]
            _match_actions("final", f.actions, actual, failures)
        if f.handoffs is not None:
            actual_h = [{"reason": h.reason, "urgency": h.urgency.value} for h in s.scalars(select(HumanHandoff))]
            for exp in f.handoffs:
                if not any(all(h.get(k) == v for k, v in exp.items()) for h in actual_h):
                    failures.append(f"final: expected handoff {exp}, got {actual_h}")
            if not f.handoffs and actual_h:
                failures.append(f"final: expected no handoff, got {actual_h}")
        if f.quotes is not None:
            actual_q = sorted(q.status.value for q in s.scalars(select(Quote)))
            if actual_q != sorted(f.quotes):
                failures.append(f"final: quotes {actual_q}, expected {sorted(f.quotes)}")
        if f.transactions is not None:
            actual_t = sorted(t.action.status.value for t in s.scalars(select(ExternalTransaction)))
            if actual_t != sorted(f.transactions):
                failures.append(f"final: transactions {actual_t}, expected {sorted(f.transactions)}")
        if f.bookings is not None:
            actual_b = sorted(f"{t.action.action_type}:{t.action.status.value}"
                              for t in s.scalars(select(ExternalTransaction)))
            if actual_b != sorted(f.bookings):
                failures.append(f"final: bookings {actual_b}, expected {sorted(f.bookings)}")
        if f.plan is not None:
            from app.trip.itinerary import effective_status

            actual_p = sorted(effective_status(s, it) for it in s.scalars(select(ItineraryItem)))
            if actual_p != sorted(f.plan):
                failures.append(f"final: plan {actual_p}, expected {sorted(f.plan)}")
        if f.dead_jobs is not None:
            dead = len(list(s.scalars(select(Job).where(Job.status == JobStatus.DEAD))))
            if dead != f.dead_jobs:
                failures.append(f"final: {dead} dead jobs, expected {f.dead_jobs}")
        mocks = _mock_providers(container)
        if f.provider_bookings is not None:
            n = sum(m.active_bookings for m in mocks)
            if n != f.provider_bookings:
                failures.append(f"final: provider holds {n} bookings, expected {f.provider_bookings}")
        if f.provider_submit_calls_max is not None:
            n = sum(m.submit_calls for m in mocks)
            if n > f.provider_submit_calls_max:
                failures.append(f"final: provider received {n} submit calls, at most {f.provider_submit_calls_max}")
        for se in f.stays:
            slug = slugs.get(se.property or scenario.properties[0], se.property)
            guest = s.scalar(select(Guest).where(Guest.external_id == se.guest_id))
            prop_id = container.properties.get(slug).property_id
            stays = [] if guest is None else list(s.scalars(
                select(Stay).where(Stay.guest_id == guest.id, Stay.property_id == prop_id).order_by(Stay.created_at)))
            p = f"final stay[{se.property or 'default'}]"
            if se.count is not None and len(stays) != se.count:
                failures.append(f"{p}: {len(stays)} stays, expected {se.count}")
            if not stays:
                if se.count != 0:
                    failures.append(f"{p}: no stay found")
                continue
            stay = stays[-1]
            facts = stay.facts or {}
            for key, sub in (se.facts or {}).items():
                fact = facts.get(key)
                if fact is None:
                    failures.append(f"{p}: fact {key!r} not remembered (facts: {sorted(facts)})")
                    continue
                for k, v in sub.items():
                    actual_v = fact.get(k)
                    if k == "value_endswith":
                        if not str(fact.get("value")).endswith(str(v)):
                            failures.append(f"{p}: fact {key}.value {fact.get('value')!r} does not end with {v!r}")
                    elif actual_v != v:
                        failures.append(f"{p}: fact {key}.{k} = {actual_v!r}, expected {v!r}")
            for key in se.facts_absent:
                if key in facts:
                    failures.append(f"{p}: fact {key!r} must not be stored (got {facts[key]})")
            if se.facts_empty and facts:
                failures.append(f"{p}: expected no facts, got {sorted(facts)}")
            if se.party_size_is_none and stay.party_size is not None:
                failures.append(f"{p}: authoritative party_size was set to {stay.party_size} from conversation")
            if se.party_size is not None and stay.party_size != se.party_size:
                failures.append(f"{p}: party_size {stay.party_size} != {se.party_size}")
            if se.status and stay.status.value != se.status:
                failures.append(f"{p}: status {stay.status.value} != {se.status}")


# ------------------------------------------------------------------- run
def run_scenario(scenario: Scenario) -> ScenarioResult:
    result = ScenarioResult(scenario=scenario)
    start = time.perf_counter()
    for env in ("DEMO_TRANSFERS_CALLBACK_SECRET", "DEMO_PREMIUM_CALLBACK_SECRET", "DEMO_GUIDES_CALLBACK_SECRET",
                "DEMO_RENTALS_CALLBACK_SECRET"):
        os.environ[env] = CALLBACK_SECRET
    try:
        with tempfile.TemporaryDirectory() as tmp:
            container, slugs = _build(scenario, Path(tmp))
            clock = container.clock
            last_stay: dict[str, str | None] = {}

            def drain_and_collect(guest_id: str) -> list[str]:
                container.worker.drain(advance_clock=True)
                sent = [m["text"] for m in container.dev_outbox.outbox(guest_id)]
                container.dev_outbox.clear()
                for text in sent:
                    result.transcript.append(Turn("bot", text, {"kind": "async_notification"}))
                return sent

            for i, step in enumerate(scenario.steps, start=1):
                prefix = f"step {i}"
                if step.advance_minutes is not None:
                    clock.advance(minutes=step.advance_minutes)  # type: ignore[attr-defined]
                    result.transcript.append(Turn("clock", f"+{step.advance_minutes} min"))
                    continue
                if step.provider_callback is not None:
                    cb = step.provider_callback
                    with container.session_factory() as s:
                        q = select(ExternalTransaction).order_by(ExternalTransaction.created_at.desc())
                        if cb.reference.startswith("auto:"):   # latest transaction of ANOTHER provider
                            q = q.join(ExternalProvider).where(ExternalProvider.slug == cb.reference[5:])
                        txn = s.scalars(q).first()
                        reference = (txn.provider_reference if txn else None) if cb.reference.startswith("auto") \
                            else cb.reference
                        stay_id = txn.action.stay_id if txn else None
                        body = json.dumps({"event_id": cb.event_id, "reference": reference or "none",
                                           "event": cb.event}).encode()
                        ts = int(clock.now().timestamp()) + cb.timestamp_offset_seconds
                        headers = {"X-Provider-Timestamp": str(ts)}
                        if cb.signature != "missing":
                            secret = CALLBACK_SECRET if cb.signature == "valid" else "attacker-secret"
                            headers["X-Provider-Signature"] = sign(secret, ts, body)
                        out = handle_callback(s, container.txn_deps, slugs[scenario.properties[0]], cb.provider,
                                              headers, body)
                        s.commit()
                    result.transcript.append(Turn("provider", f"[callback {cb.event} {cb.event_id} "
                                                              f"sig={cb.signature}] -> {out.status_code} {out.body}"))
                    exp = step.expect
                    if exp.callback_status is not None and out.status_code != exp.callback_status:
                        result.failures.append(f"{prefix}: callback HTTP {out.status_code}, expected {exp.callback_status}")
                    if exp.callback_result is not None and out.body.get("status") != exp.callback_result:
                        result.failures.append(f"{prefix}: callback result {out.body}, expected {exp.callback_result}")
                    sent = drain_and_collect(step.guest_id)
                    _check_transactions(prefix, exp, container, set(), sent, result.failures)
                    for text in sent:
                        _authority_check(prefix, text, container, stay_id, result.failures)
                    continue
                if step.guest is not None:
                    slug = slugs.get(step.property, step.property) if step.property else None
                    with container.session_factory() as s:
                        before = {a.id for a in s.scalars(select(Action))}
                        before_quotes = {q.id for q in s.scalars(select(Quote))}
                        before_txns = {t.id for t in s.scalars(select(ExternalTransaction))}
                        codes = [q.code for q in s.scalars(select(Quote).order_by(Quote.created_at))]
                    text = re.sub(r"\{quote_code:(-?\d+)\}", lambda m: codes[int(m.group(1))], step.guest)
                    reply = container.orchestrator.handle(InboundMessage(
                        channel=step.channel, sender_id=step.guest_id, text=text, property_slug=slug,
                        external_id=step.external_id))
                    if reply.duplicate:
                        result.transcript.append(Turn("guest", text, {"redelivery": step.external_id}))
                        if step.expect.silent is False:
                            result.failures.append(f"{prefix}: redelivered message was dropped as duplicate")
                        _check_transactions(prefix, step.expect, container, before_quotes,
                                            drain_and_collect(step.guest_id), result.failures)
                        continue
                    last_stay[step.guest_id] = reply.stay_id
                    result.transcript.append(Turn("guest", step.guest, {"property": slug or "default"}))
                    result.transcript.append(Turn("bot", reply.text, {
                        "intent": reply.intent, "language": reply.language, "grounded": reply.grounded,
                        "sources": reply.sources, "handed_off": reply.handed_off, "outcomes": reply.outcomes,
                        "actions": [a.detail for a in reply.actions]}))
                    _check_guest_step(prefix, step.expect, reply, container, before, result.failures)
                    _authority_check(prefix, reply.text, container, reply.stay_id, result.failures,
                                     quoted_sources=reply.sources, property_slug=reply.property_slug,
                                     locale=reply.language)
                    sent = drain_and_collect(step.guest_id)
                    _check_transactions(prefix, step.expect, container, before_quotes, sent, result.failures,
                                        before_txns)
                    for text in sent:
                        _authority_check(prefix, text, container, reply.stay_id, result.failures)
                elif step.staff_transition is not None:
                    with container.session_factory() as s:
                        action = s.scalars(select(Action).order_by(Action.created_at.desc())).first()
                        if action is None:
                            result.failures.append(f"{prefix}: no action to transition")
                            continue
                        out = transition_action(s, container.executors, action, ActionStatus(step.staff_transition.to),
                                                "staff:eval", step.staff_transition.note,
                                                transport_for=container.transport_for)
                        s.commit()
                        stay_id = action.stay_id
                    result.transcript.append(Turn("staff", f"[{step.staff_transition.to}]"))
                    result.transcript.append(Turn("bot", out.notification, {"kind": "status_notification"}))
                    exp = step.expect
                    _check_text(prefix, out.notification, exp.notification_contains, [], exp.notification_forbidden,
                                result.failures)
                    _authority_check(prefix, out.notification, container, stay_id, result.failures)
                elif step.stay_update is not None:
                    stay_id = last_stay.get(step.guest_id)
                    with container.session_factory() as s:
                        stay = s.get(Stay, stay_id) if stay_id else None
                        if stay is None:
                            result.failures.append(f"{prefix}: no stay to update")
                            continue
                        if step.stay_update.status:
                            stay.status = StayStatus(step.stay_update.status)
                        if step.stay_update.party_size is not None:
                            stay.party_size = step.stay_update.party_size
                        s.commit()
                    result.transcript.append(Turn("staff", f"[stay update {step.stay_update.model_dump(exclude_none=True)}]"))
            _check_final(scenario, container, slugs, result.failures)
            container.engine.dispose()
    except Exception as exc:  # a crash is a failure with a reason, not a harness crash
        result.error = f"{type(exc).__name__}: {exc}"
    result.seconds = time.perf_counter() - start
    return result


def run_all(scenarios: list[Scenario]) -> list[ScenarioResult]:
    configure_logging("ERROR")
    return [run_scenario(s) for s in scenarios]
