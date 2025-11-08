# app.py
from fastapi import FastAPI, Query
from mightstone.services.scryfall import Scryfall
from mightstone.services.edhrec import EDHRec
from mightstone.core.cache import install_cache

import os
CACHE_DIR = os.getenv("MIGHTSTONE_CACHE", "/var/mightstone/cache")
install_cache(path=CACHE_DIR)

app = FastAPI()
scry = Scryfall(sync=True)
edh  = EDHRec(sync=True)

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/cards/search")
def cards_search(q: str, limit: int = 25):
    res = scry.cards.search(q)[:limit]
    return [{"name": c.name, "id": c.id, "type_line": c.type_line, "ci": c.color_identity, "cmc": c.cmc} for c in res]

@app.get("/commander/summary")
def commander_summary(name: str = Query(...)):
    c = scry.cards.search(f'!"{name}" legal:commander')[0]
    avg  = edh.commanders.average_deck(name)
    high = edh.commanders.high_synergy_cards(name)
    return {
        "commander": {"name": c.name, "id": c.id, "ci": c.color_identity, "oracle_text": c.oracle_text},
        "edhrec": {
            "avg_count": len(getattr(avg, "cards", []) or []),
            "high_synergy": [x.name for x in getattr(high, "cards", []) or []][:40]
        }
    }
