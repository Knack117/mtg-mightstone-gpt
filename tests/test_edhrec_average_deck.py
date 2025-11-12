from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

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
        data = fetch_average_deck(name, bracket)
    except EdhrecError as exc:
        pytest.skip(f"EDHREC fetch failed: {exc}")

    assert data["bracket"] == bracket
    assert data["source_url"].startswith("https://edhrec.com/average-decks/")

    cards = data["cards"]
    assert isinstance(cards, list)
    assert 95 <= len(cards) <= 100
    assert all("name" in card and "qty" in card for card in cards)

    if data.get("commander_card"):
        assert "name" in data["commander_card"]
