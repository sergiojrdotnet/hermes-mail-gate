"""Mail Gate backend, mounted by Hermes at /api/plugins/hermes-mail-gate/ for the dashboard and Desktop."""
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mail_gate.paths import read_env_file, profile_dir  # noqa: E402
from mail_gate.release import ACTIONS, SCOPES, apply_rule, decide_sender  # noqa: E402
from mail_gate.routes import mail_bots  # noqa: E402
from mail_gate.store import GLOBAL_BOT, Store  # noqa: E402

router = APIRouter()
OUTCOMES = ("delivered", "released", "held", "dropped-blocked", "dropped-unauthenticated")


def _store() -> Store:
    return Store()


def _bot(value: str) -> str:
    if value != GLOBAL_BOT and value not in mail_bots():
        raise HTTPException(status_code=400, detail=f"unknown bot '{value}'")
    return value


@router.get("/summary")
async def summary() -> dict[str, Any]:
    store = _store()
    try:
        pending = store.pending()
        today = store.counters("day", 1)
    finally:
        store.close()
    by_bot: dict[str, int] = {}
    for row in pending:
        by_bot[row["bot"]] = by_bot.get(row["bot"], 0) + 1
    totals: dict[str, int] = {}
    for row in today:
        totals[row["outcome"]] = totals.get(row["outcome"], 0) + row["total"]
    return {"pending_senders": len(pending), "pending_by_bot": by_bot,
            "pending_keys": [f"{row['bot']}|{row['sender']}" for row in pending], "today": totals}


@router.get("/bots")
async def bots() -> dict[str, Any]:
    return {"bots": [{"bot": bot, "address": read_env_file(profile_dir(bot) / ".env").get("EMAIL_ADDRESS", "")}
                     for bot in mail_bots()]}


@router.get("/pending")
async def pending() -> dict[str, Any]:
    store = _store()
    try:
        return {"pending": store.pending()}
    finally:
        store.close()


@router.post("/decide")
async def decide(body: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    try:
        return decide_sender(store, _bot(str(body.get("bot", ""))), str(body.get("sender", "")),
                             str(body.get("scope", "")), str(body.get("action", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        store.close()


@router.get("/rules")
async def rules(bot: Optional[str] = Query(None)) -> dict[str, Any]:
    store = _store()
    try:
        found = store.all_rules()
    finally:
        store.close()
    return {"rules": [rule for rule in found if not bot or rule["bot"] in (bot, GLOBAL_BOT)],
            "scopes": SCOPES, "actions": ACTIONS}


@router.post("/rules")
async def add_rule(body: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    try:
        return apply_rule(store, _bot(str(body.get("bot", ""))), str(body.get("scope", "")),
                          str(body.get("value", "")), str(body.get("action", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        store.close()


@router.post("/rules/delete")
async def delete_rule(body: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    try:
        removed = store.delete_rule(_bot(str(body.get("bot", ""))), str(body.get("scope", "")),
                                    str(body.get("value", "")))
    finally:
        store.close()
    return {"removed": removed}


@router.get("/events")
async def events(bot: Optional[str] = Query(None), outcome: Optional[str] = Query(None),
                 limit: int = Query(100, ge=1, le=500), before: Optional[int] = Query(None)) -> dict[str, Any]:
    if outcome and outcome not in OUTCOMES:
        raise HTTPException(status_code=400, detail="unknown outcome")
    store = _store()
    try:
        return {"events": store.events(bot=bot or None, outcome=outcome or None, limit=limit, before_id=before)}
    finally:
        store.close()


@router.get("/stats")
async def stats(period: str = Query("day"), buckets: int = Query(30, ge=1, le=120),
                bot: Optional[str] = Query(None)) -> dict[str, Any]:
    if period not in ("day", "week", "month"):
        raise HTTPException(status_code=400, detail="period must be day|week|month")
    store = _store()
    try:
        return {"period": period, "outcomes": OUTCOMES, "rows": store.counters(period, buckets, bot or None)}
    finally:
        store.close()
