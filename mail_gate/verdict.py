from typing import Any, Iterable, Optional

from mail_gate.store import GLOBAL_BOT


def _result(section: Any) -> str:
    if isinstance(section, dict):
        status = section.get("status")
        if isinstance(status, dict):
            return str(status.get("result") or "").lower()
    return ""


def aligned(domain: str, other: str) -> bool:
    domain, other = domain.lower().rstrip("."), other.lower().rstrip(".")
    return bool(domain and other) and (domain == other or domain.endswith("." + other) or other.endswith("." + domain))


def authentication(payload: dict[str, Any], sender_domain: str) -> tuple[bool, str]:
    """Trust Forward Email's own verdicts: DMARC pass, or, for a sender publishing no DMARC policy,
    an SPF or DKIM pass aligned with the From domain."""
    dmarc = _result(payload.get("dmarc"))
    spf = _result(payload.get("spf"))
    dkim_results = (payload.get("dkim") or {}).get("results") or []
    dkim_pass = [r.get("signingDomain", "") for r in dkim_results if _result(r) == "pass"]
    dkim = "pass" if dkim_pass else (_result(dkim_results[0]) if dkim_results else "none")
    summary = f"dmarc={dmarc or 'none'} spf={spf or 'none'} dkim={dkim}"
    if dmarc == "pass":
        return True, summary
    if dmarc not in ("", "none"):
        return False, summary
    spf_domain = str((payload.get("spf") or {}).get("domain") or "")
    if spf == "pass" and aligned(spf_domain, sender_domain):
        return True, summary
    if any(aligned(domain, sender_domain) for domain in dkim_pass):
        return True, summary
    return False, summary


def decide(rules: Iterable[Any], bot: str, sender: str, sender_domain: str) -> Optional[str]:
    """Most specific rule wins: this bot's address, this bot's domain, then the same for every bot."""
    index = {(rule["bot"], rule["scope"], rule["value"]): rule["action"] for rule in rules}
    for key in ((bot, "address", sender), (bot, "domain", sender_domain),
                (GLOBAL_BOT, "address", sender), (GLOBAL_BOT, "domain", sender_domain)):
        if key in index:
            return index[key]
    return None
