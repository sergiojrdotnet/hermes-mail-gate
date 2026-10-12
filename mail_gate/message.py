import hashlib
import re
from typing import Any, Optional

MAX_TEXT_CHARS = 20_000
_TAG = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\n{3,}")


def _addresses(field: Any) -> list[dict[str, str]]:
    if not isinstance(field, dict):
        return []
    return [{"address": str(v.get("address") or "").lower(), "name": str(v.get("name") or "")}
            for v in field.get("value") or [] if isinstance(v, dict) and v.get("address")]


def single_sender(payload: dict[str, Any]) -> Optional[dict[str, str]]:
    senders = _addresses(payload.get("from"))
    return senders[0] if len(senders) == 1 and "@" in senders[0]["address"] else None


def _body(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        html = payload.get("html") if isinstance(payload.get("html"), str) else ""
        text = _TAG.sub("", html)
    text = _BLANK_LINES.sub("\n\n", text.replace("\r\n", "\n")).strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + f"\n\n[truncated: {len(text) - MAX_TEXT_CHARS} more characters in the mailbox copy]"
    return text


def _references(payload: dict[str, Any]) -> list[str]:
    value = payload.get("references")
    if isinstance(value, str):
        return value.split()
    return [str(v) for v in value] if isinstance(value, list) else []


def summarize(payload: dict[str, Any], bot: str, mailbox: str) -> dict[str, Any]:
    sender = single_sender(payload) or {"address": "", "name": ""}
    message_id = payload.get("messageId")
    if not isinstance(message_id, str) or not message_id.strip():
        digest = hashlib.sha256(repr((sender, payload.get("subject"), payload.get("date"))).encode()).hexdigest()[:24]
        message_id = f"<missing-{digest}@mail-gate>"
    return {
        "bot": bot,
        "mailbox": mailbox,
        "from": sender["address"],
        "from_name": sender["name"],
        "from_domain": sender["address"].rsplit("@", 1)[-1] if "@" in sender["address"] else "",
        "to": ", ".join(a["address"] for a in _addresses(payload.get("to"))),
        "cc": ", ".join(a["address"] for a in _addresses(payload.get("cc"))),
        "subject": str(payload.get("subject") or "(no subject)"),
        "date": str(payload.get("date") or (payload.get("session") or {}).get("arrivalDate") or ""),
        "message_id": message_id,
        "in_reply_to": str(payload.get("inReplyTo") or ""),
        "references": _references(payload),
        "text": _body(payload),
        "attachments": [],
        "attachments_text": "none",
        "size": None,
    }


def describe_attachments(attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return "none"
    return "; ".join(f"{a['name']} ({a['type']}, {a['size']} bytes{', inline' if a.get('inline') else ''})"
                     for a in attachments)
