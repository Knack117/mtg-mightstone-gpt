from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


def test_tool_registered():
    from tools_registry import TOOL_REGISTRY

    assert "edhrec_average_deck" in TOOL_REGISTRY


def test_bracket_aliases():
    from handlers.edhrec_average_deck import BRACKET_MAP

    assert BRACKET_MAP["1"] == "exhibition"
    assert BRACKET_MAP["5"] == "cedh"
