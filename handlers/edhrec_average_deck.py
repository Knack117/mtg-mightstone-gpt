import os
from typing import Tuple

import requests
from urllib.parse import urlencode

MIGHTSTONE_BASE = os.getenv("MIGHTSTONE_BASE", "https://mtg-mightstone-gpt.onrender.com")
TIMEOUT = 25
VALID_BRACKETS = {"precon", "upgraded"}


def edhrec_average_deck(name: str, bracket: str = "upgraded") -> Tuple[dict, int]:
    if not name or not name.strip():
        return {"error": {"message": "Missing 'name'"}}, 400

    normalized_bracket = (bracket or "upgraded").strip().lower()
    if normalized_bracket not in VALID_BRACKETS:
        return {"error": {"message": "Bracket must be 'precon' or 'upgraded'"}}, 400

    params = urlencode({"name": name, "bracket": normalized_bracket})
    url = f"{MIGHTSTONE_BASE}/edhrec/average-deck?{params}"

    try:
        response = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return {"error": {"message": f"Error contacting Mightstone service: {exc}"}}, 502

    try:
        payload = response.json()
    except ValueError:
        return {"error": {"message": "Malformed response from Mightstone service"}}, 502

    if response.status_code >= 400:
        return payload, response.status_code

    return payload, response.status_code or 200
