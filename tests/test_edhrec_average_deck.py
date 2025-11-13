from pathlib import Path
import sys

import pytest
import requests
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from edhrec import (
    find_average_deck_url,
    display_average_deck_bracket,
    normalize_average_deck_bracket,
)
from services.edhrec import (
    EdhrecError,
    average_deck_url,
    fetch_average_deck,
    slugify_commander,
)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Jodah, the Unifier", "jodah-the-unifier"),
        ("Donatello, the Brains // Michelangelo, the Heart", "donatello-the-brains-michelangelo-the-heart"),
        ("  Atraxa, Praetors' Voice  ", "atraxa-praetors-voice"),
    ],
)
def test_slugify_commander(name, expected):
    assert slugify_commander(name) == expected


@pytest.mark.parametrize(
    "name, bracket, expected",
    [
        ("Jodah, the Unifier", "upgraded", "https://edhrec.com/average-decks/jodah-the-unifier/upgraded"),
        ("Donatello, the Brains // Michelangelo, the Heart", "exhibition", "https://edhrec.com/average-decks/donatello-the-brains-michelangelo-the-heart/exhibition"),
        ("Donatello, the Brains // Michelangelo, the Heart", "all", "https://edhrec.com/average-decks/donatello-the-brains-michelangelo-the-heart"),
        ("Atraxa, Praetors' Voice", "exhibitition", "https://edhrec.com/average-decks/atraxa-praetors-voice/exhibition"),
    ],
)
def test_average_deck_url(name, bracket, expected):
    assert average_deck_url(name, bracket) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("all", ""),
        ("", ""),
        ("precon", "exhibition"),
        ("exhibition/budget", "exhibition/budget"),
        ("cedh-expensive", "cedh/expensive"),
        ("exhibitition", "exhibition"),
        ("exhibitition/expensive", "exhibition/expensive"),
    ],
)
def test_normalize_average_deck_bracket(value, expected):
    assert normalize_average_deck_bracket(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("", "all"),
        ("exhibition/budget", "exhibition/budget"),
    ],
)
def test_display_average_deck_bracket(value, expected):
    assert display_average_deck_bracket(value) == expected


@pytest.mark.parametrize(
    "name, bracket",
    [
        ("Jodah, the Unifier", "upgraded"),
        ("Donatello, the Brains // Michelangelo, the Heart", "upgraded"),
    ],
)
def test_average_deck_fetch_smoke(name, bracket):
    try:
        data = fetch_average_deck(name=name, bracket=bracket)
    except (EdhrecError, requests.RequestException) as exc:
        pytest.skip(f"EDHREC fetch failed: {exc}")

    assert data["bracket"] == bracket
    assert data["source_url"].startswith("https://edhrec.com/average-decks/")

    cards = data["cards"]
    assert isinstance(cards, list)
    assert 95 <= len(cards) <= 100
    assert all("name" in card and "qty" in card for card in cards)
    assert isinstance(data.get("commander_tags"), list)
    assert isinstance(data.get("commander_high_synergy_cards"), list)
    assert isinstance(data.get("commander_top_cards"), list)
    assert isinstance(data.get("commander_game_changers"), list)

    if data.get("commander_card"):
        assert "name" in data["commander_card"]


def test_average_deck_fetch_with_source_url():
    url = "https://edhrec.com/average-decks/jodah-the-unifier/upgraded"
    try:
        data = fetch_average_deck(source_url=url)
    except (EdhrecError, requests.RequestException) as exc:
        pytest.skip(f"EDHREC fetch failed: {exc}")
    assert data["source_url"] == url
    assert data["bracket"] == "upgraded"
    assert isinstance(data.get("commander_tags"), list)
    assert isinstance(data.get("commander_high_synergy_cards"), list)
    assert isinstance(data.get("commander_top_cards"), list)
    assert isinstance(data.get("commander_game_changers"), list)


def test_jodah_upgraded_discovers_url():
    session = requests.Session()
    try:
        out = find_average_deck_url(session, "Jodah, the Unifier", "upgraded")
    except requests.RequestException as exc:
        pytest.skip(f"EDHREC discovery failed: {exc}")
    finally:
        session.close()
    assert out["source_url"].endswith("/jodah-the-unifier/upgraded")


def test_tmnt_partner_pair_discovers_url():
    session = requests.Session()
    try:
        out = find_average_deck_url(
            session,
            "Donatello, the Brains // Michelangelo, the Heart",
            "upgraded",
        )
    except requests.RequestException as exc:
        pytest.skip(f"EDHREC discovery failed: {exc}")
    finally:
        session.close()
    assert "/average-decks/" in out["source_url"]
    assert out["source_url"].endswith("/upgraded")


def test_bracket_unavailable_surfaces_choices():
    session = requests.Session()
    try:
        with pytest.raises(ValueError) as excinfo:
            find_average_deck_url(session, "Jodah, the Unifier", "nonexistent")
    except requests.RequestException as exc:
        pytest.skip(f"EDHREC discovery failed: {exc}")
    finally:
        session.close()

    detail = excinfo.value.args[0]
    assert detail["code"] == "BRACKET_UNSUPPORTED"
    assert "allowed_brackets" in detail


def test_average_deck_endpoint_separates_commander_sections(monkeypatch):
    from app import app

    sample_payload = {
        "cards": [{"name": "Sol Ring", "qty": 1}],
        "commander_card": {"name": "Atraxa, Praetors' Voice"},
        "commander": "Atraxa, Praetors' Voice",
        "bracket": "upgraded",
        "source_url": "https://edhrec.com/average-decks/atraxa-praetors-voice/upgraded",
        "commander_tags": ["Proliferate", "Angels"],
        "commander_high_synergy_cards": ["Evolution Sage", "Tekuthal, Inquiry Dominus"],
        "commander_top_cards": ["Sol Ring"],
        "commander_game_changers": ["Inexorable Tide"],
    }

    def fake_fetch_average_deck(**kwargs):
        return sample_payload

    monkeypatch.setattr("services.edhrec.fetch_average_deck", fake_fetch_average_deck)
    monkeypatch.setattr("app.fetch_average_deck", fake_fetch_average_deck)

    with TestClient(app) as client:
        response = client.get(
            "/edhrec/average-deck",
            params={"name": "Atraxa, Praetors' Voice", "bracket": "upgraded"},
        )

    assert response.status_code == 200
    payload = response.json()
    meta = payload["meta"]

    assert meta["commander_tags"] == sample_payload["commander_tags"]
    assert meta["commander_high_synergy_cards"] == sample_payload["commander_high_synergy_cards"]
    assert meta["commander_top_cards"] == sample_payload["commander_top_cards"]
    assert meta["commander_game_changers"] == sample_payload["commander_game_changers"]

    # Ensure the endpoint does not merge the commander sections into the tag list.
    assert set(meta["commander_tags"]).isdisjoint(meta["commander_high_synergy_cards"])
