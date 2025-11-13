from __future__ import annotations

from typing import Any, Dict, Tuple

import requests

from services.edhrec import EdhrecError, fetch_average_deck


def _format_detail(detail: Any) -> Any:
    return detail if isinstance(detail, (dict, list)) else str(detail)


def edhrec_average_deck(name: str, bracket: str = "upgraded") -> Tuple[Dict[str, Any], int]:
    session = requests.Session()
    try:
        try:
            payload = fetch_average_deck(
                name=(name or ""),
                bracket=(bracket or ""),
                session=session,
            )
        except ValueError as exc:
            detail = _format_detail(exc.args[0] if exc.args else str(exc))
            return {"detail": detail}, 400
        except EdhrecError as exc:
            return {"error": exc.to_dict()}, 200
        except Exception as exc:
            return {"detail": f"Failed to fetch average deck: {exc}"}, 502

        response: Dict[str, Any] = {
            "cards": payload.get("cards", []),
            "commander_card": payload.get("commander_card"),
            "meta": {
                "source_url": payload.get("source_url"),
                "resolved_bracket": payload.get("bracket"),
                "request": {
                    "name": name,
                    "bracket": bracket,
                    "source_url": None,
                },
                "commander_tags": payload.get("commander_tags", []),
                "commander_high_synergy_cards": payload.get("commander_high_synergy_cards", []),
                "commander_top_cards": payload.get("commander_top_cards", []),
                "commander_game_changers": payload.get("commander_game_changers", []),
            },
            "error": None,
        }

        if payload.get("commander"):
            response["meta"]["commander"] = payload["commander"]
        if "available_brackets" in payload:
            response["meta"]["available_brackets"] = payload["available_brackets"]

        return response, 200
    finally:
        session.close()
