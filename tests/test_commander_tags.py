from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils.edhrec_commander import (
    extract_commander_sections_from_json,
    extract_commander_tags_from_html,
    extract_commander_tags_from_json,
    normalize_commander_tags,
)


HTML_SAMPLE = """
<section>
  <h2>Tags</h2>
  <div class="commander-tags">
    <a class="chip" href="/themes/five-color-goodstuff">Five-Color Goodstuff</a>
    <a href="/tags/ramp/naya">Ramp</a>
    <span>
      <a href="/average-decks/jodah-the-unifier/upgraded">Ignore me</a>
    </span>
    <a href="https://edhrec.com/tags/legendary-matters">Legendary Matters</a>
  </div>
</section>
<section>
  <h2>High Synergy Cards</h2>
  <a href="/cards/example-card">Not a tag</a>
</section>
"""


JSON_SAMPLE = {
    "props": {
        "pageProps": {
            "commander": {
                "themes": [
                    {"name": "Legendary Matters"},
                    {"label": "Cascade Value"},
                ],
                "metadata": {
                    "tagCloud": {
                        "tags": [
                            "Ramp",
                            {"title": "Five-Color Goodstuff"},
                            {"slug": "not-used"},
                        ]
                    }
                },
                "highSynergyCards": {
                    "cards": [
                        {"card": {"name": "Farseek"}},
                        {"card": {"name": "Nature's Lore"}},
                    ]
                },
                "topCards": [
                    {"name": "Sol Ring"},
                    {"name": "Arcane Signet"},
                ],
                "gameChangers": {
                    "items": [
                        {"cardName": "Jodah, the Unifier"},
                        {"card": {"names": ["Atraxa", "Praetors' Voice"]}},
                    ]
                },
            }
        }
    }
}


JSON_SAMPLE_WITH_GROUPS = {
    "props": {
        "pageProps": {
            "commander": {
                "themes": [
                    {"name": "Legendary Matters"},
                ],
                "metadata": {
                    "tagCloud": {
                        "tabs": [
                            {"id": "themes", "name": "Themes"},
                            {"id": "kindred", "name": "Kindred"},
                        ],
                        "sections": [
                            {
                                "name": "Themes",
                                "tags": [
                                    {"name": "Token Swarm"},
                                ],
                            },
                            {
                                "name": "Kindred",
                                "tags": [
                                    {"name": "Squirrel"},
                                ],
                            },
                        ],
                    }
                },
            }
        }
    }
}


JSON_SAMPLE_WITH_STRUCTURAL_NAMES = {
    "props": {
        "pageProps": {
            "commander": {
                "themes": [],
                "metadata": {
                    "tagCloud": {
                        "tagGroups": [
                            {
                                "name": "Themes",
                                "tags": [
                                    {"name": "Token Swarm"},
                                    {"label": "Go Wide"},
                                ],
                            },
                            {
                                "name": "Kindred",
                                "items": [
                                    {"name": "Squirrel"},
                                ],
                            },
                        ],
                        "groups": [
                            {
                                "name": "Card Types",
                                "items": [
                                    {"name": "Creatures"},
                                    {"name": "Instants"},
                                ],
                            }
                        ],
                    }
                },
            }
        }
    }
}


def test_extract_commander_tags_from_html():
    tags = extract_commander_tags_from_html(HTML_SAMPLE)
    assert tags == ["Five-Color Goodstuff", "Ramp", "Legendary Matters"]


def test_extract_commander_tags_from_json():
    tags = extract_commander_tags_from_json(JSON_SAMPLE)
    assert tags == ["Legendary Matters", "Cascade Value", "Ramp", "Five-Color Goodstuff"]


def test_extract_commander_tags_from_json_ignores_group_labels():
    tags = extract_commander_tags_from_json(JSON_SAMPLE_WITH_GROUPS)
    assert tags == ["Legendary Matters", "Token Swarm", "Squirrel"]


def test_extract_commander_tags_from_json_filters_structural_names():
    tags = extract_commander_tags_from_json(JSON_SAMPLE_WITH_STRUCTURAL_NAMES)
    assert tags == ["Token Swarm", "Go Wide", "Squirrel"]


def test_extract_commander_sections_from_json():
    sections = extract_commander_sections_from_json(JSON_SAMPLE)
    assert sections["High Synergy Cards"] == ["Farseek", "Nature's Lore"]
    assert sections["Top Cards"] == ["Sol Ring", "Arcane Signet"]
    assert sections["Game Changers"] == ["Jodah, the Unifier", "Atraxa // Praetors' Voice"]


def test_normalize_commander_tags_deduplicates():
    tags = normalize_commander_tags([
        "Ramp",
        " ramp ",
        "Legendary Matters",
        "LEGENDARY MATTERS",
        "",
        "12345",
        "Themes",
        "Creatures",
    ])
    assert tags == ["Ramp", "Legendary Matters"]
