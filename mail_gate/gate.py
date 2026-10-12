"""Webhook route script: Forward Email's JSON on stdin, a decision on stdout.

Hermes drops the webhook on ``[SILENT]``, empty output or a non-zero exit, and replaces the payload with any
JSON object printed here, which the route's prompt template then renders for the bot.
"""
import json
import sys
import traceback
from typing import Any, Optional

from mail_gate import mailbox
from mail_gate.config import load_config
from mail_gate.message import describe_attachments, single_sender, summarize
from mail_gate.paths import data_dir
from mail_gate.store import Store
from mail_gate.verdict import authentication, decide

SILENT = "[SILENT]"
RESTRICTED, TRUSTED = "mail", "mail-trusted"


def _enrich(mail: dict[str, Any]) -> dict[str, Any]:
    try:
        stored = mailbox.describe(mail["bot"], mail["message_id"])
    except Exception:
        stored = None
    if stored is None:
        mail["attachments_text"] = "unknown (the mailbox copy was not found)"
        return mail
    mail["size"] = stored.get("size")
    mail["attachments"] = stored.get("attachments") or []
    mail["attachments_text"] = ("not listed (message larger than 15 MB; open it in the mailbox)"
                                if stored.get("too_large") else describe_attachments(mail["attachments"]))
    return mail


def _thread(mail: dict[str, Any]) -> str:
    try:
        earlier = mailbox.thread_messages(mail["bot"], mail.get("references") or [])
    except Exception:
        earlier = []
    if not earlier:
        return "(no earlier messages of this thread were found in the mailbox)"
    return "\n\n".join(f"--- {item['date']} · {item['from']} · {item['subject']}\n{item['text']}" for item in earlier)


def _deliver(store: Store, bot: str, route: str, outcome: str, mail: dict[str, Any], auth: str) -> str:
    _enrich(mail)
    store.log(bot, route, outcome, mail, auth)
    return json.dumps({"mail": mail, "thread": _thread(mail)})


def evaluate(store: Store, bot: str, mode: str, payload: dict[str, Any], owners: list[str]) -> str:
    release = (payload.get("mail_gate") or {}).get("release") if isinstance(payload.get("mail_gate"), dict) else None
    if release is not None:
        return _release(store, bot, int(release))
    sender = single_sender(payload)
    recipients = payload.get("recipients") or [""]
    mail = summarize(payload, bot, str((payload.get("session") or {}).get("recipient") or recipients[0]))
    if not store.mark_seen(bot, mode, mail["message_id"]):
        return SILENT
    authenticated, auth = authentication(payload, mail["from_domain"]) if sender else (False, "no single From address")
    is_owner = bool(sender) and mail["from"] in owners
    if mode == TRUSTED:
        if not (authenticated and is_owner):
            return SILENT
        return _deliver(store, bot, mode, "delivered", mail, auth)
    if is_owner and authenticated:
        return SILENT
    if not authenticated:
        store.log(bot, mode, "dropped-unauthenticated", mail, auth)
        return SILENT
    action = decide(store.rules_for(bot), bot, mail["from"], mail["from_domain"])
    if action == "block":
        store.log(bot, mode, "dropped-blocked", mail, auth)
        return SILENT
    if action == "allow":
        return _deliver(store, bot, mode, "delivered", mail, auth)
    store.hold(bot, mail, auth)
    store.log(bot, mode, "held", mail, auth)
    return SILENT


def _release(store: Store, bot: str, held_id: int) -> str:
    row = store.held_by_id(held_id)
    if row is None or row["bot"] != bot:
        return SILENT
    mail = json.loads(row["mail"])
    action = decide(store.rules_for(bot), bot, mail["from"], mail["from_domain"])
    if action is None:
        return SILENT
    store.drop_held(held_id)
    if action == "block":
        store.log(bot, RESTRICTED, "dropped-blocked", mail, row["auth"] or "")
        return SILENT
    return _deliver(store, bot, RESTRICTED, "released", mail, row["auth"] or "")


def main(bot: str, mode: str, stdin_text: Optional[str] = None) -> int:
    store: Optional[Store] = None
    try:
        payload = json.loads(stdin_text if stdin_text is not None else sys.stdin.read())
        if not isinstance(payload, dict):
            print(SILENT)
            return 0
        store = Store()
        store.purge()
        print(evaluate(store, bot, mode, payload, load_config().owners))
        return 0
    except Exception:
        with open(data_dir() / "gate-errors.log", "a") as handle:
            handle.write(f"[{bot}/{mode}] {traceback.format_exc()}\n")
        print(SILENT)
        return 0
    finally:
        if store is not None:
            store.close()
