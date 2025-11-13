# services/edhrec.py

from typing import Optional, Dict, Any, List
import re, requests, time
from bs4 import BeautifulSoup
from utils.commander_identity import commander_to_slug
from utils.edhrec_commander import extract_build_id_from_html

# Add to the list of exported names
__all__.append("fetch_commander_summary")

def _parse_count(value: str) -> Optional[int]:
    """Parse strings like '7.6K', '900', '3M' into integers."""
    if not value:
        return None
    v = value.lower().replace(",", "")
    mult = 1
    if v.endswith("k"):
        mult, v = 1000, v[:-1]
    elif v.endswith("m"):
        mult, v = 1_000_000, v[:-1]
    try:
        return int(float(v) * mult)
    except ValueError:
        return None

def _extract_tags_with_counts(html: str) -> List[Dict[str, Any]]:
    """Extract tags with deck counts from the HTML page."""
    tags = []
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").lower()
        if "/tags/" not in href and "/themes/" not in href:
            continue
        text = anchor.get_text(" ").strip()
        if not text:
            continue
        parts = text.split()
        count = None
        if parts and re.match(r"^[\\d.].*", parts[-1]):
            count = _parse_count(parts[-1])
            name = " ".join(parts[:-1]) if len(parts) > 1 else parts[-1]
        else:
            name = text
        if name and not any(t["name"].lower() == name.lower() for t in tags):
            tags.append({"name": name, "deck_count": count})
    return tags

def _parse_cardlists_from_json(payload: dict) -> Dict[str, List[Dict[str, Any]]]:
    """Parse card lists from the JSON response."""
    categories = {}
    page_props = payload.get("props", {}).get("pageProps", {})
    cardlists = page_props.get("data", {}).get("container", {}).get("json_dict", {}).get("cardlists")
    
    if not cardlists:
        return categories
    
    for section in cardlists:
        header = section.get("header") or section.get("name") or section.get("title")
        if not isinstance(header, str):
            continue
        header = header.strip()
        cards_out = []
        for key in ("cardviews", "cards", "items"):
            cardlist = section.get(key)
            if isinstance(cardlist, list):
                for entry in cardlist:
                    name = entry.get("name") or entry.get("cardName") or entry.get("label")
                    if not name:
                        continue
                    name = name.strip()
                    synergy = entry.get("synergy")
                    synergy_pct = round(float(synergy) * 100, 2) if isinstance(synergy, (int, float)) else None
                    num_decks = entry.get("num_decks") or entry.get("numDecks") or entry.get("inclusion")
                    potential_decks = entry.get("potential_decks") or entry.get("potentialDecks")
                    inclusion_pct = None
                    if isinstance(num_decks, (int, float)) and isinstance(potential_decks, (int, float)) and potential_decks:
                        inclusion_pct = round(float(num_decks) / float(potential_decks) * 100, 2)
                    cards_out.append({
                        "name": name,
                        "inclusion_percent": inclusion_pct,
                        "synergy_percent": synergy_pct
                    })
        if cards_out:
            categories[header] = cards_out
    return categories

def fetch_commander_summary(name: str, *, budget: Optional[str] = None, session: Optional[requests.Session] = None) -> Dict[str, Any]:
    """Fetch detailed summary for a commander including categories, tags, and budget info."""
    if not name:
        raise ValueError("Commander name is required")
    slug = commander_to_slug(name.strip())
    own = False
    if session is None:
        session = requests.Session()
        own = True
    try:
        # Fetch the commander page HTML to get tags & buildId
        url = f"https://edhrec.com/commanders/{slug}"
        if budget:
            budget_slug = budget.strip().lower()
            url = f"{url}/{budget_slug}"
        r = session.get(url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        html = r.text
        tags = _extract_tags_with_counts(html)
        build_id = extract_build_id_from_html(html)
        categories = {}
        if build_id:
            json_url = f"https://edhrec.com/_next/data/{build_id}/commanders/{slug}"
            if budget:
                json_url = f"{json_url}/{budget_slug}"
            json_url += ".json"
            try:
                j = session.get(json_url, headers=_HEADERS, timeout=REQUEST_TIMEOUT)
                if j.status_code == 200:
                    payload = j.json()
                    categories = _parse_cardlists_from_json(payload)
            except Exception:
                pass
        # sort tags by deck_count (largest first) and keep the top 10
        tags_sorted = sorted(tags, key=lambda t: -(t["deck_count"] or -1))
        return {
            "commander": name,
            "source_url": url,
            "budget": budget or None,
            "categories": categories,
            "tags": tags,
            "top_tags": tags_sorted[:10]
        }
    finally:
        if own:
            session.close()
