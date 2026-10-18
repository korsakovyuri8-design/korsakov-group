from app.journey import place_resolver


def test_kotor_is_resolved_for_montenegro_beta():
    place = place_resolver.resolve_place("Kotor")

    assert place is not None
    assert place["entity_key"] == "me:kotor"
    assert place["canonical_name"] == "Kotor"
    assert place["locality"] == "Kotor"
    assert place["timezone"] == "Europe/Podgorica"
    assert place["coordinates"]["lat"] is not None
    assert place["coordinates"]["lon"] is not None


def test_kotor_alias_resolves_to_same_entity():
    a = place_resolver.resolve_place("Kotor")
    b = place_resolver.resolve_place("Kotor, Montenegro")

    assert a is not None and b is not None
    assert a["entity_key"] == b["entity_key"]
