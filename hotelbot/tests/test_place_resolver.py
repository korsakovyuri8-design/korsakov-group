from app.journey import place_resolver


def test_portonovi_resolves():
    p = place_resolver.resolve_place("One&Only Portonovi")

    assert p["status"] == "resolved"
    assert p["entity_key"] == "me:oneandonly_portonovi"
    assert p["canonical_name"] == "One&Only Portonovi"
    assert p["locality"] == "Kumbor"
    assert p["municipality"] == "Herceg Novi"
    assert p["country"] == "Montenegro"
    assert p["timezone"] == "Europe/Podgorica"
    assert p["coordinates"] == {
        "lat": 42.435287,
        "lon": 18.59857,
    }


def test_known_alias_resolves():
    assert (
        place_resolver.resolve_place("One & Only Portonovi")["entity_key"]
        == "me:oneandonly_portonovi"
    )


def test_unknown_place_is_never_guessed():
    assert place_resolver.resolve_place("Portonovi") is None
    assert place_resolver.resolve_place("Hotel near Portonovi") is None


def test_podgorica_city_resolves_for_route_origin():
    p = place_resolver.resolve_place("Podgorica")

    assert p["entity_key"] == "me:podgorica"
    assert p["timezone"] == "Europe/Podgorica"
    assert p["coordinates"] == {
        "lat": 42.44123775,
        "lon": 19.26308552,
    }
