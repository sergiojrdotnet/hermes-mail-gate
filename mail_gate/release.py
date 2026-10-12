"""Apply a rule, then re-run every held message it touches through the gate.

Each held message is offered to both routes: the gate re-evaluates the most specific rule, so only the matching
route delivers it (or drops it on a block), and a message no rule covers yet simply stays held.
"""
import hashlib
import hmac
import json
import urllib.request
from typing import Any

from mail_gate.paths import default_env
from mail_gate.routes import SECRET_ENV, route_name
from mail_gate.store import Store

GATEWAY_URL = "http://127.0.0.1:8644"
SCOPES = ("address", "domain")
ACTIONS = ("allow", "block", "trust")


def _post(bot: str, mode: str, held_id: int, secret: str) -> int:
    body = json.dumps({"mail_gate": {"release": held_id}}).encode()
    request = urllib.request.Request(
        f"{GATEWAY_URL}/p/{bot}/webhooks/{route_name(mode, bot)}", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Webhook-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.status


def apply_rule(store: Store, bot: str, scope: str, value: str, action: str) -> dict[str, Any]:
    if scope not in SCOPES or action not in ACTIONS:
        raise ValueError("scope must be address|domain and action allow|block|trust")
    value = value.strip().lower()
    if not value or (scope == "address") != ("@" in value):
        raise ValueError("an address rule needs a full address, a domain rule a bare domain")
    store.set_rule(bot, scope, value, action)
    secret = default_env().get(SECRET_ENV, "")
    processed = 0
    for row in store.held_matching(bot, scope, value):
        for mode in ("mail", "mail-trusted"):
            if store.held_by_id(row["id"]) is not None:
                _post(row["bot"], mode, row["id"], secret)
        processed += 1
    return {"rule": {"bot": bot, "scope": scope, "value": value, "action": action}, "held_processed": processed,
            "still_held": len(store.held_matching(bot, scope, value))}


def decide_sender(store: Store, bot: str, sender: str, scope: str, action: str) -> dict[str, Any]:
    sender = sender.strip().lower()
    return apply_rule(store, bot, scope, sender if scope == "address" else sender.rsplit("@", 1)[-1], action)
