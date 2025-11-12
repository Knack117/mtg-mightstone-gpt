from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils.identity import canonicalize_identity


def test_code_roundtrips():
    assert canonicalize_identity("b")[0] == "b"
    assert canonicalize_identity("wur")[0] == "wur"
    assert canonicalize_identity("rgu")[0] == "urg"


def test_label_inputs():
    assert canonicalize_identity("Jeskai")[0] == "wur"
    assert canonicalize_identity("Mono Black")[0] == "b"


def test_slug_inputs():
    assert canonicalize_identity("jeskai")[2] == "jeskai"
    assert canonicalize_identity("mono-black")[0] == "b"


def test_invalid_identity():
    with pytest.raises(ValueError):
        canonicalize_identity("xyz")
