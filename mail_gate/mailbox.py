import email
import imaplib
import mimetypes
import smtplib
import ssl
import time
from contextlib import contextmanager
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any, Iterator, Optional

Account = tuple[str, str]

from mail_gate.paths import profile_env

IMAP_HOST = "imap.forwardemail.net"
IMAP_PORT = 993
MAX_FETCH_BYTES = 15_000_000
SMTP_HOST = "smtp.forwardemail.net"
SMTP_PORT = 465
SENT_FOLDER = "Sent"
JUNK_FOLDER = "Junk"


def credentials(bot: str) -> Account:
    env = profile_env(bot)
    address, password = env.get("EMAIL_ADDRESS", ""), env.get("EMAIL_PASSWORD", "")
    if not address or not password:
        raise RuntimeError(f"{bot} has no EMAIL_ADDRESS/EMAIL_PASSWORD in its profile .env")
    return address, password


@contextmanager
def imap_session(account: Account, folder: str = "INBOX", readonly: bool = True) -> Iterator[imaplib.IMAP4_SSL]:
    address, password = account
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=20)
    try:
        imap.login(address, password)
        status, _ = imap.select(_quote(folder), readonly=readonly)
        if status != "OK":
            raise RuntimeError(f"cannot open folder {folder}")
        yield imap
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def decoded(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def find_uid(imap: imaplib.IMAP4_SSL, message_id: str) -> Optional[bytes]:
    status, data = imap.uid("SEARCH", None, "HEADER", "Message-ID", message_id)
    if status != "OK" or not data or not data[0]:
        return None
    return data[0].split()[-1]


def attachments_of(message: Message) -> list[dict[str, Any]]:
    found = []
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = (part.get("Content-Disposition") or "").lower()
        if not filename and "attachment" not in disposition:
            continue
        payload = part.get_payload(decode=True) or b""
        found.append({
            "name": decoded(filename) or f"unnamed.{part.get_content_subtype()}",
            "type": part.get_content_type(),
            "size": len(payload),
            "inline": disposition.startswith("inline") or (bool(part.get("Content-ID")) and "attachment" not in disposition),
        })
    return found


def describe(account: Account, message_id: str, attempts: int = 4, delay: float = 1.5) -> Optional[dict[str, Any]]:
    """Size and attachment list of a stored message; the mailbox copy can land a moment after the webhook."""
    with imap_session(account) as imap:
        for attempt in range(attempts):
            uid = find_uid(imap, message_id)
            if uid:
                status, data = imap.uid("FETCH", uid, "(RFC822.SIZE)")
                size = int(data[0].decode().split("RFC822.SIZE")[1].strip(" )").split()[0]) if status == "OK" else None
                if size and size > MAX_FETCH_BYTES:
                    return {"uid": uid.decode(), "size": size, "attachments": [], "too_large": True}
                status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                message = email.message_from_bytes(data[0][1])
                return {"uid": uid.decode(), "size": size, "attachments": attachments_of(message)}
            if attempt < attempts - 1:
                time.sleep(delay)
                imap.noop()
    return None


def thread_messages(account: Account, references: list[str], limit: int = 5, max_chars: int = 4000) -> list[dict[str, str]]:
    """Earlier messages of a thread found in this mailbox, oldest first, for one-shot webhook runs."""
    found: list[dict[str, str]] = []
    if not references:
        return found
    for folder in ("INBOX", "Sent"):
        try:
            with imap_session(account, folder) as imap:
                for message_id in references[-limit:]:
                    uid = find_uid(imap, message_id)
                    if not uid:
                        continue
                    status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                    if status != "OK":
                        continue
                    message = email.message_from_bytes(data[0][1])
                    found.append({"message_id": message_id, "from": decoded(message.get("From")),
                                  "date": message.get("Date", ""), "subject": decoded(message.get("Subject")),
                                  "text": _plain_text(message)[:max_chars]})
        except RuntimeError:
            continue
    order = {message_id: index for index, message_id in enumerate(references)}
    return sorted(found, key=lambda item: order.get(item["message_id"], 0))


def _plain_text(message: Message) -> str:
    for part in message.walk():
        if part.get_content_type() == "text/plain" and "attachment" not in (part.get("Content-Disposition") or ""):
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace").strip()
    return ""



def send(account: Account, to: list[str], subject: str, text: str, html: Optional[str] = None, cc: Optional[list[str]] = None,
         attachments: Optional[list[str]] = None, in_reply_to: str = "", references: Optional[list[str]] = None,
         sender_name: str = "") -> str:
    address, password = account
    message = EmailMessage()
    message["From"] = formataddr((sender_name, address)) if sender_name else address
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=address.rsplit("@", 1)[-1])
    if in_reply_to:
        thread = list(references or [])
        if in_reply_to not in thread:
            thread.append(in_reply_to)
        message["In-Reply-To"] = in_reply_to
        message["References"] = " ".join(thread)
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    for raw_path in attachments or []:
        path = Path(raw_path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=60) as smtp:
        smtp.login(address, password)
        smtp.send_message(message)
    try:
        with imap_session(account, SENT_FOLDER, readonly=False) as imap:
            imap.append(_quote(SENT_FOLDER), "(\\Seen)", None, message.as_bytes())
    except Exception:
        pass
    return str(message["Message-ID"])


def move_to_junk(account: Account, message_id: str) -> bool:
    with imap_session(account, readonly=False) as imap:
        uid = find_uid(imap, message_id)
        if not uid:
            return False
        status, _ = imap.uid("MOVE", uid, _quote(JUNK_FOLDER))
        if status != "OK":
            imap.uid("COPY", uid, _quote(JUNK_FOLDER))
            imap.uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)")
            imap.expunge()
        return True


def read(account: Account, message_id: str) -> Optional[dict[str, Any]]:
    with imap_session(account) as imap:
        uid = find_uid(imap, message_id)
        if not uid:
            return None
        status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
        message = email.message_from_bytes(data[0][1])
        return {"message_id": message_id, "from": decoded(message.get("From")), "to": decoded(message.get("To")),
                "cc": decoded(message.get("Cc")), "date": message.get("Date", ""),
                "subject": decoded(message.get("Subject")), "text": _plain_text(message),
                "attachments": attachments_of(message)}


def save_attachment(account: Account, message_id: str, name: str, directory: str) -> Optional[str]:
    with imap_session(account) as imap:
        uid = find_uid(imap, message_id)
        if not uid:
            return None
        status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
        message = email.message_from_bytes(data[0][1])
    for part in message.walk():
        if part.is_multipart() or decoded(part.get_filename()) != name:
            continue
        target_dir = Path(directory)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / Path(name).name
        target.write_bytes(part.get_payload(decode=True) or b"")
        return str(target)
    return None


def headers(account: Account, message_id: str) -> Optional[dict[str, Any]]:
    with imap_session(account) as imap:
        uid = find_uid(imap, message_id)
        if not uid:
            return None
        status, data = imap.uid("FETCH", uid, "(BODY.PEEK[HEADER])")
        message = email.message_from_bytes(data[0][1])
    return {"subject": decoded(message.get("Subject")),
            "references": (message.get("References") or "").split()}
