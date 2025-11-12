from pathlib import Path
import sys

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils.commander_identity import commander_to_slug
from utils.edhrec_average import (
    build_average_url,
    fetch_average_deck,
    parse_decklines_from_text,
)


def test_slugging_examples():
    assert commander_to_slug("Avatar Aang") == "avatar-aang"
    assert commander_to_slug("Chulane, Teller of Tales") == "chulane-teller-of-tales"
    assert commander_to_slug("K’rrik, Son of Yawgmoth") == "krrik-son-of-yawgmoth"


def test_slug_for_turtles_in_a_half_shell():
    slug = commander_to_slug("Donatello, the Brains // Michelangelo, the Heart")
    assert slug == "donatello-the-brains-michelangelo-the-heart"


def test_url_building_variants():
    slug, url, label = build_average_url("Avatar Aang", "all")
    assert slug == "avatar-aang"
    assert url == "https://edhrec.com/average-decks/avatar-aang"
    assert label == "All"

    _, url, _ = build_average_url("Avatar Aang", "exhibition")
    assert url.endswith("/exhibition")

    _, url, _ = build_average_url("Avatar Aang", "2")
    assert url.endswith("/core")

    _, url, _ = build_average_url("Avatar Aang", "5")
    assert url.endswith("/cedh")


def test_parse_decklines():
    sample = """
    Commander (1)
    1 Avatar Aang
    Creatures (22)
    1 Llanowar Elves
    2 Elvish Mystic
    1 Sol Ring
    38 Forest
    """
    items = parse_decklines_from_text(sample)
    names = {item["name"] for item in items}
    assert {"Avatar Aang", "Forest"}.issubset(names)
    assert any(item["count"] == 2 and item["name"] == "Elvish Mystic" for item in items)


def test_fetch_average_deck_handles_partner_slug(monkeypatch):
    called_urls = []

    deck_lines = [
        "Commander (1)",
        "1 Donatello, the Brains // Michelangelo, the Heart",
    ] + [f"1 Card{i}" for i in range(1, 101)]
    html = "<html><body><pre>" + "\n".join(deck_lines) + "</pre></body></html>"

    class DummyResponse:
        def __init__(self, status_code: int, text: str = "") -> None:
            self.status_code = status_code
            self.text = text

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code} error")

    def fake_get(url, *args, **kwargs):
        called_urls.append(url)
        if url.endswith("donatello-the-brains-michelangelo-the-heart"):
            return DummyResponse(404, "Not Found")
        if url.endswith("donatello-the-brains"):
            return DummyResponse(200, html)
        raise AssertionError(f"Unexpected URL fetched: {url}")

    monkeypatch.setattr("utils.edhrec_average.requests.get", fake_get)

    payload = fetch_average_deck("Donatello, the Brains // Michelangelo, the Heart")
    items = payload["container"]["collections"][0]["items"]

    assert len(items) >= 60
    assert any(url.endswith("donatello-the-brains") for url in called_urls)
    assert called_urls[0].endswith("donatello-the-brains-michelangelo-the-heart")
