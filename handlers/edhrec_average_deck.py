import os
import requests
from urllib.parse import urlencode

MIGHTSTONE_BASE = os.getenv("MIGHTSTONE_BASE", "https://mtg-mightstone-gpt.onrender.com")
TIMEOUT = 25

BRACKET_MAP = {
    "1": "exhibition",
    "2": "core",
    "3": "upgraded",
    "4": "optimized",
    "5": "cedh",
}

def edhrec_average_deck(name: str, bracket: str = "all"):
    if not name or not name.strip():
        return {"detail": "Missing 'name'"}, 400

    b = (bracket or "all").strip().lower()
    b = BRACKET_MAP.get(b, b) or "all"

    url = f"{MIGHTSTONE_BASE}/edhrec/average-deck?{urlencode({'name': name, 'bracket': b})}"
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()
