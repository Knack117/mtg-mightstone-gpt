from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils.edhrec_commander import (
    extract_commander_tags_from_html,
    extract_commander_tags_from_json,
    normalize_commander_tags,
)


HTML_SAMPLE = """
<div class="commander-tags">
  <a class="chip" href="/themes/five-color-goodstuff">Five-Color Goodstuff</a>
  <a href="/tags/ramp/naya">Ramp</a>
  <a href="/average-decks/jodah-the-unifier/upgraded">Ignore me</a>
  <a href="https://edhrec.com/tags/legendary-matters">Legendary Matters</a>
</div>
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


def test_normalize_commander_tags_deduplicates():
    tags = normalize_commander_tags([
        "Ramp",
        " ramp ",
        "Legendary Matters",
        "LEGENDARY MATTERS",
        "",
        "12345",
    ])
    assert tags == ["Ramp", "Legendary Matters"]
