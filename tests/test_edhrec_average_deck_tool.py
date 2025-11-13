from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


def test_tool_registered():
    from tools_registry import TOOL_REGISTRY

    assert "edhrec_average_deck" in TOOL_REGISTRY


def test_handler_uses_service(monkeypatch):
    from handlers.edhrec_average_deck import edhrec_average_deck

    captured = {}

    def fake_fetch_average_deck(*, name, bracket, session):
        captured["name"] = name
        captured["bracket"] = bracket
        return {
            "commander": "Atraxa, Praetors' Voice",
            "bracket": "upgraded",
            "source_url": "https://edhrec.com/average-decks/atraxa/upgraded",
            "cards": [{"name": "Atraxa, Praetors' Voice", "qty": 1}],
            "commander_card": {"name": "Atraxa, Praetors' Voice", "qty": 1},
            "commander_tags": ["Counters"],
            "commander_high_synergy_cards": ["Card A"],
            "commander_top_cards": ["Card B"],
            "commander_game_changers": ["Card C"],
            "available_brackets": ["precon", "upgraded"],
        }

    monkeypatch.setattr(
        "handlers.edhrec_average_deck.fetch_average_deck", fake_fetch_average_deck
    )

    payload, status = edhrec_average_deck("Atraxa, Praetors' Voice", "upgraded")

    assert status == 200
    assert captured == {"name": "Atraxa, Praetors' Voice", "bracket": "upgraded"}
    assert payload["meta"]["resolved_bracket"] == "upgraded"
    assert payload["meta"]["available_brackets"] == ["precon", "upgraded"]
    assert payload["error"] is None
