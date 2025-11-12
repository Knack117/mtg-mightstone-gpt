from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


def test_tool_registered():
    from tools_registry import TOOL_REGISTRY

    assert "edhrec_average_deck" in TOOL_REGISTRY


def test_valid_brackets():
    from handlers.edhrec_average_deck import VALID_BRACKETS

    assert VALID_BRACKETS == {"precon", "upgraded"}
