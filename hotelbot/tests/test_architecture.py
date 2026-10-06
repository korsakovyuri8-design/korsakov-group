"""The two worlds stay decoupled.

LOCAL WORLD (discovery: Place / Event)  -  app/discovery, app/places (minus the pack loader)
TRANSACTION WORLD (Provider / Offering / Quote / Transaction)  -  app/transactions, app/marketplace

Neither imports the other; app/marketplace/bridge.py is the one seam, used by
the orchestration layer (app/trip, app/agent)."""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import select

from app.agent.operations import Operation, classify_operations, layer
from app.capabilities.registry import CapabilityRegistry, CapabilitySpec
from app.db.models import Offering, Place
from app.marketplace import bridge

APP = Path(__file__).resolve().parents[1] / "app"
DISCOVERY = [APP / "discovery", APP / "places", APP / "world"]
TRANSACTION = [APP / "transactions", APP / "marketplace"]
# Loading a region pack writes both worlds (it is a data file of the region).
EXEMPT = {APP / "places" / "pack.py", APP / "marketplace" / "bridge.py"}


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
    return out


def _violations(sources: list[Path], forbidden: tuple[str, ...]) -> list[str]:
    bad = []
    for root in sources:
        for f in root.rglob("*.py"):
            if f in EXEMPT:
                continue
            bad += [f"{f.relative_to(APP)} imports {m}" for m in _imports(f) if m.startswith(forbidden)]
    return bad


def test_discovery_world_does_not_import_transactions():
    assert _violations(DISCOVERY, ("app.transactions", "app.marketplace")) == []


def test_transaction_world_does_not_import_discovery():
    assert _violations(TRANSACTION, ("app.discovery", "app.places.hours", "app.shared.geo", "app.trip",
                                     "app.world")) == []


def test_neutral_modules_belong_to_neither_world():
    """Time parsing, geo and identifiers are shared by both worlds and must
    not pull either one in."""
    neutral = [APP / "shared", APP / "nlp" / "temporal.py"]
    bad = []
    for root in neutral:
        for f in ([root] if root.suffix == ".py" else root.rglob("*.py")):
            bad += [f"{f.relative_to(APP)} imports {m}" for m in _imports(f)
                    if m.startswith(("app.transactions", "app.marketplace", "app.discovery", "app.places",
                                     "app.world", "app.trip"))]
    assert bad == []


def test_trip_reaches_the_marketplace_only_through_the_bridge():
    bad = []
    for f in (APP / "trip").rglob("*.py"):
        bad += [f"{f.relative_to(APP)} imports {m}" for m in _imports(f)
                if m.startswith("app.marketplace") and m != "app.marketplace"
                or m == "app.marketplace" and "bridge" not in f.read_text(encoding="utf-8")]
    assert bad == []
    # plan / preference / reference logic is pure: no world access at all
    for name in ("selection.py", "schedule.py", "preferences.py"):
        mods = _imports(APP / "trip" / name)
        assert not {m for m in mods if m.startswith(("app.transactions", "app.marketplace"))}, name


def test_world_sources_do_not_know_the_transaction_world():
    assert _violations([APP / "world"], ("app.transactions", "app.marketplace", "app.trip", "app.agent",
                                         "app.llm")) == []


def test_world_fabric_resolves_truth_without_a_model():
    """No LLM anywhere in the fabric: entity resolution and field resolution
    are deterministic code + policy config."""
    assert _violations([APP / "world"], ("app.llm", "app.agent.responder", "openai", "anthropic")) == []


def test_discovery_reads_the_world_only_through_its_repository():
    bad = []
    for f in (APP / "discovery").rglob("*.py"):
        if f.name == "repository.py":
            continue
        bad += [f"{f.relative_to(APP)} imports {m}" for m in _imports(f)
                if m.startswith("app.world") and m not in ("app.world.records",)]
    assert bad == []


def test_place_only_entities_have_no_transaction_side(container):
    """A pharmacy, an ATM, a viewpoint are Places - never providers or offerings."""
    with container.session_factory() as s:
        for sub in ("pharmacy", "atm", "viewpoint", "supermarket", "car_park"):
            for place in s.scalars(select(Place).where(Place.subcategory == sub)):
                assert bridge.options_for(s, place=place) == [], place.slug
        restaurant = s.scalar(select(Place).where(Place.slug == "restoran-demo-ponoc"))
        assert [o.service_type for o in bridge.options_for(s, place=restaurant)] == ["restaurant_reservation"]
        # a taxi has no Place at all
        assert all(o.place_id is None for o in s.scalars(select(Offering).where(Offering.service_type == "taxi")))


def test_transaction_capabilities_gate_the_bridge(container):
    with container.session_factory() as s:
        restaurant = s.scalar(select(Place).where(Place.slug == "restoran-demo-ponoc"))
        no_tables = CapabilityRegistry(CapabilitySpec(services={}))
        assert bridge.options_for(s, place=restaurant, capabilities=no_tables) == []


def test_discovery_capabilities_are_separate(chat, container):
    runtime = container.properties.get("example-hotel")
    runtime.capabilities.spec.discovery.categories = ["FOOD", "NIGHTLIFE"]
    try:
        reply = chat("Where is the nearest pharmacy?")
        assert "Demo Apoteka" not in (reply.text or "")
        assert "Restoran Demo Jezero" in (chat("Recommend a restaurant with vegan options", guest="g2").text or "")
    finally:
        runtime.capabilities.spec.discovery.categories = None
    described = runtime.capabilities.describe()
    assert set(described) >= {"discovery_capabilities", "transaction_capabilities"}


def test_operations_classifier():
    assert layer(classify_operations("Where is the nearest pharmacy?")) == "discovery"
    assert classify_operations("save 2") == {Operation.SAVE}
    assert layer(classify_operations("Cancel the guide")) == "execution"
    assert Operation.CHANGE in classify_operations("Move my taxi to 7am")
    assert layer(classify_operations("Find a restaurant and book a table")) == "both"
