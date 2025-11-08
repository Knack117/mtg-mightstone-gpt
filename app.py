# app.py
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional
import os
import time

import httpx

from mightstone.core.cache import install_cache
from mightstone.services.scryfall import Scryfall
from mightstone.services.edhrec import EdhRecStatic  # Mightstone ≥ 0.12

# -----------------------------------------------------------------------------
# Config / Cache
# -----------------------------------------------------------------------------
CACHE_DIR = os.getenv("MIGHTSTONE_CACHE", "/var/mightstone/cache")
install_cache(path=CACHE_DIR)

APP_UA = os.getenv("APP_USER_AGENT", "MTG-Deckbuilder/1.0 (+contact@example.com)")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))

SPELLBOOK_BASE = "https://backend.commanderspellbook.com/api/combos"

# -----------------------------------------------------------------------------
# App + clients
# -----------------------------------------------------------------------------
app = FastAPI(title="MTG Mightstone Adapter", version="1.2")

# Allow your GPT/actions or localhost to call this (tighten later if desired)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

scry = Scryfall()          # Sync-friendly client
edh  = EdhRecStatic()      # Static EDHREC client (no proxy required)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _card_lite(c) -> Dict[str, Any]:
    """Lean card dict for GPT consumption."""
    return {
        "name": c.name,
        "id": c.id,
        "type_line": getattr(c, "type_line", None),
        "ci": getattr(c, "color_identity", None),
        "cmc": getattr(c, "cmc", None),
        "set": getattr(c, "set", None),
        "set_name": getattr(c, "set_name", None),
        "collector_number": getattr(c, "collector_number", None),
    }

def _extract_named_list(obj, candidate_attrs: List[str]) -> List[str]:
    """
    EDHREC page models can change; try a few attribute names and
    return a unique-ordered list of card names.
    """
    for attr in candidate_attrs:
        val = getattr(obj, attr, None)
        if not val:
            continue
        names: List[str] = []
        try:
            for item in val:
                n = getattr(item, "name", item)
                if isinstance(n, str):
                    names.append(n)
        except TypeError:
            continue
        if names:
            seen, out = set(), []
            for n in names:
                if n not in seen:
                    seen.add(n); out.append(n)
            return out
    return []

def _spellbook_search(q: str, limit: int) -> List[Dict[str, Any]]:
    """
    Call Commander Spellbook with a friendly User-Agent and exponential backoff on 429.
    """
    headers = {"User-Agent": APP_UA, "Accept": "application/json"}
    lim = str(max(1, min(int(limit), 100)))
    params = {"limit": lim}
    if q:
        params["q"] = q

    for attempt in range(5):  # 0,1,2,3,4 -> up to ~7.5s total backoff
        r = httpx.get(SPELLBOOK_BASE, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        if r.status_code != 429:
            r.raise_for_status()
            payload = r.json()
            return payload.get("data", payload)  # API may return {"data":[...]} or bare list
        time.sleep(0.5 * (2 ** attempt))

    raise HTTPException(status_code=429, detail="Commander Spellbook rate limited; try again shortly.")

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "cache_dir": CACHE_DIR,
        "services": {"scryfall": True, "edhrec": True, "spellbook": True},
        "ua": APP_UA,
    }

@app.get("/cards/search")
def cards_search(q: str = Query(..., description="Scryfall search string"), limit: int = 25):
    try:
        res = scry.cards.search(q)[: max(1, min(limit, 100))]
        return [_card_lite(c) for c in res]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"search error: {e}")

@app.get("/legal_printings")
def legal_printings(name: str = Query(..., description="Exact card name")):
    try:
        base = scry.cards.search(f'!"{name}"')[0]
        prints = scry.cards.search(f'!"{base.name}" include:extras unique:prints')
        return {
            "name": base.name,
            "prints": [
                {
                    "id": p.id,
                    "set": p.set,
                    "set_name": p.set_name,
                    "collector_number": p.collector_number,
                }
                for p in prints
            ],
        }
    except IndexError:
        raise HTTPException(status_code=404, detail=f'Card not found: "{name}"')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"printings error: {e}")

@app.get("/commander/summary")
def commander_summary(name: str = Query(..., description="Commander name (exact or close)")):
    """
    Returns:
      commander: oracle + color identity
      edhrec: best-effort sections (high synergy, top cards, average deck sample)
    """
    try:
        commander = scry.cards.search(f'!"{name}" legal:commander game:paper')[0]
    except IndexError:
        raise HTTPException(status_code=404, detail=f'Commander not found: "{name}"')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"scryfall error: {e}")

    edhrec_summary: Dict[str, Any] = {
        "high_synergy": [],
        "top_cards": [],
        "average_deck_sample": [],
    }
    try:
        page = edh.commander(commander.name)
        edhrec_summary["high_synergy"] = _extract_named_list(
            page, ["high_synergy", "high_synergy_cards", "synergies"]
        )[:40]
        edhrec_summary["top_cards"] = _extract_named_list(
            page, ["top_cards", "signature", "signature_cards", "commander_cards"]
        )[:60]

        avg = edh.average_deck(commander.name)
        sample = []
        for attr in ["cards", "main", "deck", "list"]:
            if hasattr(avg, attr):
                try:
                    for item in getattr(avg, attr):
                        n = getattr(item, "name", item)
                        if isinstance(n, str):
                            sample.append(n)
                        if len(sample) >= 20:
                            break
                except TypeError:
                    pass
                break
        edhrec_summary["average_deck_sample"] = list(dict.fromkeys(sample))
    except Exception:
        # Keep EDHREC fields empty on any failure
        pass

    return {
        "commander": {
            "name": commander.name,
            "id": commander.id,
            "oracle_text": getattr(commander, "oracle_text", None),
            "type_line": getattr(commander, "type_line", None),
            "color_identity": getattr(commander, "color_identity", None),
        },
        "edhrec": edhrec_summary,
    }

@app.get("/combos")
def combos(
    commander: Optional[str] = Query(None, description='Commander filter, e.g. "Miirym, Sentinel Wyrm"'),
    includes: Optional[List[str]] = Query(None, description='One or more card names the combo must include'),
    limit: int = 25,
):
    """
    Compact Commander Spellbook combos.
    Builds a query like: commander:"Miirym, Sentinel Wyrm" includes:"Dockside Extortionist"
    """
    clauses: List[str] = []
    if commander:
        clauses.append(f'commander:"{commander}"')
    if includes:
        for n in includes:
            if n and n.strip():
                clauses.append(f'includes:"{n.strip()}"')
    q = " ".join(clauses)

    try:
        data = _spellbook_search(q, limit)
        out = []
        for c in data:
            # Collect distinct card names from "uses"/"requires" or fallback to "cards"
            names: List[str] = []
            for sec in ("uses", "requires"):
                for item in c.get(sec, []) or []:
                    nm = item.get("card")
                    if isinstance(nm, str) and nm not in names:
                        names.append(nm)
            if not names and isinstance(c.get("cards"), list):
                for nm in c["cards"]:
                    if isinstance(nm, str) and nm not in names:
                        names.append(nm)

            out.append({
                "id": c.get("id"),
                "name": c.get("name"),
                "cards": names,
                "results": c.get("results"),
                "permalink": c.get("permalink") or c.get("url"),
            })
        return out
    except httpx.HTTPStatusError as e:
        text = ""
        try:
            text = e.response.text[:200]
        except Exception:
            pass
        raise HTTPException(status_code=e.response.status_code, detail=f"spellbook error: {text}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"spellbook error: {e}")
