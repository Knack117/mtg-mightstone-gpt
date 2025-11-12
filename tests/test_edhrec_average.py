from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils.commander_identity import commander_to_slug
from utils.edhrec_average import build_average_url, parse_decklines_from_text


def test_slugging_examples():
    assert commander_to_slug("Avatar Aang") == "avatar-aang"
    assert commander_to_slug("Chulane, Teller of Tales") == "chulane-teller-of-tales"
    assert commander_to_slug("K’rrik, Son of Yawgmoth") == "krrik-son-of-yawgmoth"


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
