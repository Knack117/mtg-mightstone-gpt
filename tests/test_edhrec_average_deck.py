from pathlib import Path
import sys

import pytest
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from edhrec import find_average_deck_url
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
        ("Donatello, the Brains // Michelangelo, the Heart", "precon", "https://edhrec.com/average-decks/donatello-the-brains-michelangelo-the-heart/precon"),
    ],
)
def test_average_deck_url(name, bracket, expected):
    assert average_deck_url(name, bracket) == expected


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
    assert detail["code"] == "BRACKET_UNAVAILABLE"
    assert "available_brackets" in detail
