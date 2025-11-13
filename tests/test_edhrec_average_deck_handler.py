import pytest

from handlers import edhrec_average_deck as handler


class DummySession:
    def close(self):
        pass


@pytest.mark.parametrize("bracket_input,expected", [(1, "1"), ("2", "2")])
def test_edhrec_average_deck_coerces_numeric_bracket(monkeypatch, bracket_input, expected):
    captured = {}

    def fake_fetch_average_deck(name, bracket, session):  # noqa: ANN001
        captured["name"] = name
        captured["bracket"] = bracket
        return {
            "cards": [],
            "commander_card": None,
            "source_url": "https://edhrec.com/average-decks/test",
            "bracket": "exhibition",
            "commander": name,
            "commander_tags": [],
            "commander_high_synergy_cards": [],
            "commander_top_cards": [],
            "commander_game_changers": [],
        }

    monkeypatch.setattr(handler, "fetch_average_deck", fake_fetch_average_deck)
    monkeypatch.setattr(handler.requests, "Session", lambda: DummySession())

    response, status = handler.edhrec_average_deck("Test Commander", bracket=bracket_input)

    assert status == 200
    assert captured["bracket"] == expected
    assert response["meta"]["request"]["bracket"] == str(bracket_input)
