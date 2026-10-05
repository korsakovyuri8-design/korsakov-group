import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.db.models import HotelKnowledgeDocument
from app.db.session import create_schema, make_engine, make_session_factory
from app.knowledge.ingest import ingest_pack, load_pack
from app.knowledge.schemas import KnowledgePack
from app.knowledge.service import KnowledgeService
from tests.conftest import EXAMPLE_PACK


def _pack(items):
    return KnowledgePack.model_validate(
        {"pack": {"hotel_slug": "h", "hotel_name": "H", "source": "test", "synthetic": True}, "items": items}
    )


def _item(key, en, **kw):
    return {"key": key, "category": kw.pop("category", "misc"), "content": {"en": en}, **kw}


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    create_schema(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_example_pack_is_valid_and_fully_marked_synthetic():
    pack = load_pack(EXAMPLE_PACK)
    assert pack.pack.synthetic is True
    assert "aleksandar" not in pack.pack.hotel_name.lower()
    assert all(item.metadata.get("synthetic") is True for item in pack.items)
    assert all({"en", "cnr"} <= set(item.content) for item in pack.items)


def test_pack_rejects_duplicate_keys_and_empty_content():
    with pytest.raises(ValidationError):
        _pack([_item("a", "x"), _item("a", "y")])
    with pytest.raises(ValidationError):
        _pack([{"key": "a", "category": "c", "content": {"en": "  "}}])


def test_ingest_is_idempotent_and_tracks_changes(session):
    pack = _pack([_item("a", "Alpha"), _item("b", "Beta")])
    first = ingest_pack(session, pack)
    assert (first.created, first.updated, first.unchanged) == (2, 0, 0)

    second = ingest_pack(session, pack)
    assert (second.created, second.updated, second.unchanged) == (0, 0, 2)

    changed = _pack([_item("a", "Alpha v2")])
    third = ingest_pack(session, changed)
    assert (third.updated, third.deleted) == (1, 1)
    docs = list(session.scalars(select(HotelKnowledgeDocument)))
    assert [d.content["en"] for d in docs] == ["Alpha v2"]
    assert docs[0].extra["synthetic"] is True
    assert docs[0].source == "test"


@pytest.fixture
def knowledge(session):
    report = ingest_pack(session, load_pack(EXAMPLE_PACK))
    return KnowledgeService.from_db(session, report.hotel_id, min_score=0.5)


@pytest.mark.parametrize(
    "query,key",
    [
        ("What time is breakfast?", "breakfast"),
        ("Kad je dorucak?", "breakfast"),
        ("Is there parking?", "parking"),
        ("Mogu li doći sa psom?", "pets"),
        ("what is the wifi password", "wifi"),
        ("Koliko košta transfer sa aerodroma?", "airport_transfer"),
        ("Šta da posjetimo u blizini?", "local_durmitor"),
        ("Can we check in after 11pm?", "late_arrival_policy"),
    ],
)
def test_retrieval_finds_grounded_item(knowledge, query, key):
    result = knowledge.search(query)
    assert result.grounded
    assert result.items[0].key == key


@pytest.mark.parametrize("query", ["Do you have a sauna?", "Is there a gym?", "Do you have vegan food?", "spa and pool?"])
def test_retrieval_is_not_grounded_for_unknown_topics(knowledge, query):
    assert not knowledge.search(query).grounded


def test_retrieved_item_language_fallback(knowledge):
    item = knowledge.search("breakfast").items[0]
    assert item.text_for("cnr").startswith("Doručak")
    assert item.text_for("de") == item.content["en"]
