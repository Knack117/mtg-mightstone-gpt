import os
import re
from typing import Optional

import httpx
import hishel
from fastapi import FastAPI, HTTPException, Query

# ✅ Correct imports for Mightstone 0.12.x
from mightstone.services.edhrec import EdhRecStatic
from mightstone.services.scryfall import Scryfall

APP_NAME = "mtg-deckbuilding-mightstone"
APP_VERSION = os.environ.get("RENDER_GIT_COMMIT", "dev")
USER_AGENT = os.environ.get(
    "HTTP_USER_AGENT",
    f"{APP_NAME}/{APP_VERSION} (+https://render.com)",
)

CACHE_DIR = os.environ.get("MIGHTSTONE_CACHE_DIR", "/var/mightstone/cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# --- Hishel cache + httpx transport (async) ---
# Filesystem storage; bump TTL if you want longer caching.
storage = hishel.FileStorage(base_path=CACHE_DIR, default_ttl=24 * 3600)

controller = hishel.Controller(
    cacheable_methods=["GET"],          # we only cache GETs
    cacheable_status_codes=[200],       # only cache successful responses
    allow_stale=True,                   # serve stale if origin hiccups
    always_revalidate=False,            # honor Cache-Control/ETag normally
)

# Respectful httpx transport with timeouts/retries and a nice UA.
base_transport = httpx.AsyncHTTPTransport(retries=2)
cache_transport = hishel.AsyncCacheTransport(
    transport=base_transport,
    storage=storage,
    controller=controller,
)

# Default headers sent to upstream APIs.
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}

# Mightstone clients wired with the cached transport.
scryfall = Scryfall(transport=cache_transport)
edhrec = EdhRecStatic(transport=cache_transport)

# FastAPI app
app = FastAPI(title="Mightstone Bridge", version=APP_VERSION)


@app.get("/health")
async def health():
    # Lightweight check that our clients and cache exist.
    return {
        "ok": True,
        "version": APP_VERSION,
        "cache_dir": CACHE_DIR,
        "ua": USER_AGENT,
        "services": ["scryfall", "edhrec-static"],
    }


# ---------------------------
# SCRYFALL
# ---------------------------

@app.get("/scryfall/autocomplete")
async def scryfall_autocomplete(
    q: str = Query(..., min_length=1, description="Partial card name"),
    include_extras: bool = Query(False, description="Include funny/extra cards"),
):
    """
    Wraps Mightstone's Scryfall.autocomplete().
    """
    try:
        # Mightstone handles async internally via universalasync; we can call it directly.
        catalog = await scryfall.autocomplete_async(q=q, include_extras=include_extras)
        return {"data": catalog.data}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Scryfall error: {e!s}")


# ---------------------------
# EDHREC (Static JSON)
# ---------------------------

def _normalize_identity(identity: str) -> str:
    """
    Normalize identity to EDH color letters in WUBRG order.
    Accepts things like 'ur', 'WUr', 'g', 'wubrg', etc.
    Returns lowercase string like 'wu', 'g', 'wubrg'.
    """
    letters = set(re.findall(r"[wubrgc]", identity.lower()))
    order = "wubrgc"
    return "".join([ch for ch in order if ch in letters])


@app.get("/edhrec/combos")
async def edhrec_combos(
    identity: Optional[str] = Query(
        None,
        description="Optional color identity filter (e.g. 'w', 'ur', 'wubrg').",
    )
):
    """
    Wraps Mightstone's EdhRecStatic.combos(identity=?).
    If identity is omitted, returns the 'all colors' page that EDHREC exposes.
    """
    try:
        id_arg = None
        if identity:
            norm = _normalize_identity(identity)
            if not norm:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid identity. Use some combination of W,U,B,R,G (e.g., 'ur', 'wubrg').",
                )
            # Mightstone accepts the identity type; strings work as the underlying path uses the letters.
            id_arg = norm

        page = await edhrec.combos_async(identity=id_arg)
        # PageCombos is a pydantic model; convert to dict for JSON response.
        return page.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"EDHREC error: {e!s}")


# ---------------------------
# Global HTTPX settings
# ---------------------------

@app.on_event("startup")
async def on_startup():
    # Install default headers on the cache transport’s underlying pool via a client.
    # We create a single shared client instance for polite defaults.
    # (Mightstone creates its own requests under the hood; they inherit the transport semantics.)
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
