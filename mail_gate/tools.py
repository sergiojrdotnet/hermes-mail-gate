"""Agent tools for the bot's own mailbox (toolset ``mail_gate``)."""
import json
import re
from pathlib import Path
from typing import Any, Callable

from mail_gate import mailbox
from mail_gate.message import MAX_TEXT_CHARS
from mail_gate.store import Store

TOOLSET = "mail_gate"


def _account() -> mailbox.Account:
    from agent.secret_scope import get_secret

    address, password = get_secret("EMAIL_ADDRESS"), get_secret("EMAIL_PASSWORD")
    if not address or not password:
        raise RuntimeError("this profile has no mailbox (EMAIL_ADDRESS/EMAIL_PASSWORD)")
    return address, password


def has_mailbox() -> bool:
    try:
        _account()
        return True
    except Exception:
        return False


def _outbox() -> Path:
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "outbox"


def _ok(**fields: Any) -> str:
    return json.dumps({"ok": True, **fields})


def _guard(handler: Callable[[dict[str, Any]], str]) -> Callable[..., str]:
    def run(args: dict[str, Any], **_kwargs: Any) -> str:
        try:
            return handler(args or {})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)})

    return run


def _mail_read(args: dict[str, Any]) -> str:
    found = mailbox.read(_account(), str(args["message_id"]))
    if found is None:
        return json.dumps({"ok": False, "error": "message not found in your mailbox"})
    found["text"] = found["text"][:MAX_TEXT_CHARS]
    return _ok(message=found)


def _mail_save_attachment(args: dict[str, Any]) -> str:
    message_id = str(args["message_id"])
    folder = _outbox() / "mail" / re.sub(r"[^A-Za-z0-9._-]+", "_", message_id.strip("<>"))[:80]
    saved = mailbox.save_attachment(_account(), message_id, str(args["filename"]), str(folder))
    if saved is None:
        return json.dumps({"ok": False, "error": "message or attachment not found"})
    return _ok(path=saved)


def _within_outbox(raw: str) -> str:
    outbox = _outbox().resolve()
    path = Path(raw).expanduser().resolve()
    if not path.is_relative_to(outbox):
        raise ValueError(f"attachments must be files inside your outbox ({outbox}); copy the file there first")
    if not path.is_file():
        raise ValueError(f"attachment not found: {path}")
    return str(path)


def _mail_send(args: dict[str, Any]) -> str:
    account = _account()
    to = [str(a) for a in args.get("to") or []]
    if not to:
        raise ValueError("'to' needs at least one address")
    attachments = [_within_outbox(str(p)) for p in args.get("attachments") or []]
    subject, in_reply_to, references = str(args.get("subject") or ""), "", []
    reply_to = str(args.get("reply_to_message_id") or "")
    if reply_to:
        original = mailbox.headers(account, reply_to)
        if original is None:
            raise ValueError("reply_to_message_id was not found in your mailbox")
        in_reply_to, references = reply_to, original["references"]
        if not subject:
            subject = original["subject"] if original["subject"].lower().startswith("re:") else f"Re: {original['subject']}"
    if not subject:
        raise ValueError("'subject' is required unless replying")
    message_id = mailbox.send(account, to, subject, str(args.get("text") or ""), html=args.get("html") or None,
                              cc=[str(a) for a in args.get("cc") or []], attachments=attachments,
                              in_reply_to=in_reply_to, references=references,
                              sender_name=str(args.get("sender_name") or ""))
    return _ok(message_id=message_id)


def _mail_move_to_junk(args: dict[str, Any]) -> str:
    moved = mailbox.move_to_junk(_account(), str(args["message_id"]))
    return _ok(moved=moved) if moved else json.dumps({"ok": False, "error": "message not found in your mailbox"})


def _mail_gate_pending(_args: dict[str, Any]) -> str:
    store = Store()
    try:
        senders = [{key: row[key] for key in ("bot", "sender", "sender_domain", "messages", "first_at")}
                   for row in store.pending()]
    finally:
        store.close()
    by_bot: dict[str, int] = {}
    for row in senders:
        by_bot[row["bot"]] = by_bot.get(row["bot"], 0) + 1
    return _ok(pending_senders=len(senders), by_bot=by_bot, senders=senders,
               note="Sergio decides these in the Mail Gate plugin; they are waiting for him, not for you.")


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required}}


_MESSAGE_ID = {"type": "string", "description": "The Message-ID exactly as shown, including the angle brackets."}
_ADDRESSES = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    (_schema("mail_read", "Read one message from your own mailbox: headers, plain text and its attachment list.",
             {"message_id": _MESSAGE_ID}, ["message_id"]), _mail_read),
    (_schema("mail_save_attachment", "Save one attachment of a message in your mailbox to your outbox and return "
             "its path, so you can open it with your other tools.",
             {"message_id": _MESSAGE_ID, "filename": {"type": "string", "description": "Attachment name as listed."}},
             ["message_id", "filename"]), _mail_save_attachment),
    (_schema("mail_send", "Send an email from your own address. To reply, pass reply_to_message_id so the answer "
             "continues the same thread (subject defaults to Re: …). Attachments must be files in your outbox.",
             {"to": _ADDRESSES, "cc": _ADDRESSES, "subject": {"type": "string"},
              "text": {"type": "string", "description": "Plain-text body (always sent)."},
              "html": {"type": "string", "description": "Optional HTML version of the same body."},
              "attachments": {**_ADDRESSES, "description": "Absolute paths of files inside your outbox."},
              "reply_to_message_id": _MESSAGE_ID,
              "sender_name": {"type": "string", "description": "Display name shown next to your address."}},
             ["to", "text"]), _mail_send),
    (_schema("mail_move_to_junk", "Move a message in your mailbox to Junk (spam, phishing, irrelevant mail).",
             {"message_id": _MESSAGE_ID}, ["message_id"]), _mail_move_to_junk),
    (_schema("mail_gate_pending", "Senders waiting for Sergio's allow/block decision in the Mail Gate, per bot "
             "(addresses and domains only, never message content).", {}, []), _mail_gate_pending),
]


def register_tools(ctx: Any) -> None:
    for schema, handler in TOOLS:
        check = None if schema["name"] == "mail_gate_pending" else has_mailbox
        ctx.register_tool(name=schema["name"], toolset=TOOLSET, schema=schema, handler=_guard(handler), check_fn=check)
