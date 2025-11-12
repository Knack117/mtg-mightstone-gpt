from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils.commander_identity import slugify_commander


def test_slug_chulane():
    assert slugify_commander("Chulane, Teller of Tales") == "chulane-teller-of-tales"


def test_slug_krrik_apostrophe():
    assert slugify_commander("K’rrik, Son of Yawgmoth") == "krrik-son-of-yawgmoth"


def test_slug_mdfc():
    assert slugify_commander("Eruth, Tormented Prophet // Backside") == "eruth-tormented-prophet"
