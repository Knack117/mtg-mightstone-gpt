# app.py
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
import hishel
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Mightstone 0.12.x imports
from mightstone.services.scryfall import Scryfall
from mightstone.services.edhrec import EdhRecStatic

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
APP_NAME = "mtg-deckbuilding-mightstone"
APP_VERSION = os.environ.get("RENDER_GIT_COMMIT", "dev")
USER_AGENT = os.environ.get("HTTP_USER_AGENT", f"{APP_NAME}/{APP_VERSION} (+https://render.com)")
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "30"))

CACHE_DIR = os.environ.get("MIGHTSTONE_CACHE_DIR", "/var/mightstone/cache")
os.makedirs(CACHE_DIR, exist_ok=True)

SPELLBOOK_BASE = "https://backend.commanderspellbook.com/api/combos"

# -----------------------------------------------------------------------------
# Cached HTTP transport (Hishel + httpx)
# -----------------------------------------------------------------------------
# Minimal, compatible setup (no default_ttl arg)
storage = hishel.FileStorage(base_path=CACHE_DIR)

# Keep the controller simple and widely compatible
controller = hishel.Controller(
    cacheable_methods=["GET"],
    cacheable_status_codes=[200],
)

# Retries at the transport layer; we also do polite backoff for Spellbook
base_transport = httpx.AsyncHTTPTransport(retries=2)
cache_transport = hishel.AsyncCacheTransport(
    transport=base_transport,
    storage=storage,
    controller=controller,
)

DEFAULT_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# -----------------------------------------------------------------------------
# Clients
# -----------------------------------------------------------------------------
scry = Scryfall(transport=cache_transport)
edh = EdhRecStatic(transport=cache_transport)

# -----------------------------------------------------------------------------
# FastAPI app
# -----------------------------------------------------------------------------
app = FastAPI(title="Mightstone Bridge", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten if desired
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _card_lite(c) -> Dict[str, Any]:
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
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    lim = str(max(1, min(int(limit), 100)))
    params = {"limit": lim}
    if q:
        params["q"] = q

    for attempt in range(5):  # exponential backoff on 429
        r = httpx.get(SPELLBOOK_BASE, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        if r.status_code != 429:
            r.raise_for_status()
            payload = r.json()
            return payload.get("data", payload)
        time.sleep(0.5 * (2 ** attempt))

    raise HTTPException(status_code=429, detail="Commander Spellbook rate limited; try again shortly.")

def _normalize_identity(identity: str) -> str:
    """Return letters in WUBRG(C) order, lowercase, from inputs like 'WUr', 'g', 'wubrg'."""
    letters = set(re.findall(r"[wubrgc]", identity.lower()))
    order = "wubrgc"
    return "".join(ch for ch in order if ch in letters)

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {
        "ok": True,
        "version": APP_VERSION,
        "cache_dir": CACHE_DIR,
        "ua": USER_AGENT,
        "services": {"scryfall": True, "edhrec": True, "spellbook": True},
    }

@app.get("/cards/search")
async def cards_search(q: str = Query(..., description="Scryfall search string"), limit: int = 25):
    try:
        res = await scry.cards.search_async(q)
        res = res[: max(1, min(limit, 100))]
        return [_card_lite(c) for c in res]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"search error: {e}")

@app.get("/legal_printings")
async def legal_printings(name: str = Query(..., description="Exact card name")):
    try:
        base = (await scry.cards.search_async(f'!"{name}"'))[0]
        prints = await scry.cards.search_async(f'!"{base.name}" include:extras unique:prints')
        return {
            "name": base.name,
            "prints": [
                {"id": p.id, "set": p.set, "set_name": p.set_name, "collector_number": p.collector_number}
                for p in prints
            ],
        }
    except IndexError:
        raise HTTPException(status_code=404, detail=f'Card not found: "{name}"')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"printings error: {e}")

@app.get("/commander/summary")
async def commander_summary(name: str = Query(..., description="Commander name (exact or close)")):
    try:
        commander = (await scry.cards.search_async(f'!"{name}" legal:commander game:paper'))[0]
    except IndexError:
        raise HTTPException(status_code=404, detail=f'Commander not found: "{name}"')
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"scryfall error: {e}")

    edhrec_summary: Dict[str, Any] = {"high_synergy": [], "top_cards": [], "average_deck_sample": []}
    try:
        page = await edh.commander_async(commander.name)
        edhrec_summary["high_synergy"] = _extract_named_list(
            page, ["high_synergy", "high_synergy_cards", "synergies"]
        )[:40]
        edhrec_summary["top_cards"] = _extract_named_list(
            page, ["top_cards", "signature", "signature_cards", "commander_cards"]
        )[:60]

        avg = await edh.average_deck_async(commander.name)
        sample: List[str] = []
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
        # keep EDHREC fields empty on failure
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
async def combos(
    commander: Optional[str] = Query(None, description='Commander filter, e.g. "Miirym, Sentinel Wyrm"'),
    includes: Optional[List[str]] = Query(None, description='One or more card names the combo must include'),
    limit: int = 25,
):
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
        txt = ""
        try:
            txt = e.response.text[:200]
        except Exception:
            pass
        raise HTTPException(status_code=e.response.status_code, detail=f"spellbook error: {txt}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"spellbook error: {e}")

@app.get("/scryfall/autocomplete")
async def scryfall_autocomplete(
    q: str = Query(..., min_length=1, description="Partial card name"),
    include_extras: bool = Query(False, description="Include funny/extra cards"),
):
    try:
        catalog = await scry.autocomplete_async(q=q, include_extras=include_extras)
        return {"data": catalog.data}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Scryfall error: {e!s}")

@app.get("/edhrec/combos")
async def edhrec_combos(
    identity: Optional[str] = Query(None, description="Optional color identity (e.g. 'w', 'ur', 'wubrg')."),
):
    try:
        id_arg = None
        if identity:
            norm = _normalize_identity(identity)
            if not norm:
                raise HTTPException(status_code=400, detail="Invalid identity; use W/U/B/R/G letters.")
            id_arg = norm
        page = await edh.combos_async(identity=id_arg)
        return page.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"EDHREC error: {e!s}")

# -----------------------------------------------------------------------------
# Lifecycle: shared httpx client (optional)
# -----------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup():
    app.state.httpx_client = httpx.AsyncClient(
        transport=cache_transport,
        headers=DEFAULT_HEADERS,
        timeout=httpx.Timeout(10.0, connect=10.0),
        http2=True,
    )

@app.on_event("shutdown")
async def on_shutdown():
    client = getattr(app.state, "httpx_client", None)
    if client is not None:
        await client.aclose()
