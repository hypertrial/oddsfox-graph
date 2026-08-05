from oddsfox_graph import ids


def test_canonical_ids_are_deterministic() -> None:
    assert ids.team_id("Argentina") == ids.team_id("Argentina")
    assert ids.team_id("Türkiye") == "team:turkiye"
    assert ids.event_id("351746") == "event:351746"
    assert ids.market_id("1897133") == "market:1897133"
    assert ids.outcome_id("1897133", "Yes") == "outcome:1897133:yes"


def test_team_code_aliases() -> None:
    aliases = ids.team_aliases_from_code("tur")
    assert "Türkiye" in aliases
