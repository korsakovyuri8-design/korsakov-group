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
DISCOVERY = [APP / "discovery", APP / "places"]
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
    assert _violations(TRANSACTION, ("app.discovery", "app.places.hours", "app.places.geo", "app.trip")) == []


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
