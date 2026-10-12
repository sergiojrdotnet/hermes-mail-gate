import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TEMP_ROOT = tempfile.mkdtemp()
os.environ["MAIL_GATE_HERMES_ROOT"] = TEMP_ROOT

from mail_gate import gate  # noqa: E402
from mail_gate.store import GLOBAL_BOT, Store  # noqa: E402
from mail_gate.verdict import authentication, decide  # noqa: E402

OWNER = "owner@example.org"


def payload(sender="supplier@vendor.example", dmarc="pass", spf="pass", spf_domain=None, dkim=(("vendor.example", "pass"),),
            message_id="<m1@vendor.example>", subject="Invoice 42", to="ceo@example.org"):
    return {
        "from": {"value": [{"address": sender, "name": "Sender"}]},
        "to": {"value": [{"address": to, "name": ""}]},
        "subject": subject,
        "messageId": message_id,
        "date": "2026-10-12T15:04:43.000Z",
        "text": "Body text",
        "recipients": [to],
        "session": {"recipient": to, "arrivalDate": "2026-10-12T15:04:43.954Z"},
        "dmarc": {"status": {"result": dmarc}},
        "spf": {"domain": spf_domain or sender.split("@")[1], "status": {"result": spf}},
        "dkim": {"results": [{"signingDomain": domain, "status": {"result": result}} for domain, result in dkim]},
    }


class VerdictTest(unittest.TestCase):
    def test_dmarc_pass_is_authenticated(self):
        self.assertTrue(authentication(payload(), "vendor.example")[0])

    def test_dmarc_fail_is_rejected_even_with_spf_pass(self):
        self.assertFalse(authentication(payload(dmarc="fail"), "vendor.example")[0])

    def test_no_dmarc_policy_needs_aligned_spf_or_dkim(self):
        aligned_spf = payload(dmarc="none", dkim=())
        self.assertTrue(authentication(aligned_spf, "vendor.example")[0])
        foreign = payload(dmarc="none", spf_domain="bulk.example", dkim=(("bulk.example", "pass"),))
        self.assertFalse(authentication(foreign, "vendor.example")[0])
        aligned_dkim = payload(dmarc="none", spf="fail", dkim=(("mail.vendor.example", "pass"),))
        self.assertTrue(authentication(aligned_dkim, "vendor.example")[0])

    def test_most_specific_rule_wins(self):
        rules = [
            {"bot": "ceo", "scope": "domain", "value": "vendor.example", "action": "allow"},
            {"bot": "ceo", "scope": "address", "value": "spam@vendor.example", "action": "block"},
            {"bot": GLOBAL_BOT, "scope": "domain", "value": "other.example", "action": "block"},
            {"bot": "ceo", "scope": "address", "value": "friend@other.example", "action": "allow"},
        ]
        self.assertEqual(decide(rules, "ceo", "a@vendor.example", "vendor.example"), "allow")
        self.assertEqual(decide(rules, "ceo", "spam@vendor.example", "vendor.example"), "block")
        self.assertEqual(decide(rules, "ceo", "friend@other.example", "other.example"), "allow")
        self.assertEqual(decide(rules, "ceo", "x@other.example", "other.example"), "block")
        self.assertIsNone(decide(rules, "cto", "a@vendor.example", "vendor.example"))


@mock.patch("mail_gate.mailbox.credentials", return_value=("ceo@example.org", "secret"))
@mock.patch("mail_gate.mailbox.thread_messages", return_value=[])
@mock.patch("mail_gate.mailbox.describe",
            return_value={"uid": "5", "size": 1234, "attachments": [{"name": "a.pdf", "type": "application/pdf",
                                                                     "size": 900, "inline": False}]})
class GateTest(unittest.TestCase):
    def setUp(self):
        db = Path(TEMP_ROOT) / "mail-gate" / "mail-gate.db"
        for suffix in ("", "-wal", "-shm"):
            Path(str(db) + suffix).unlink(missing_ok=True)
        self.store = Store()
        self.store.set_rule(GLOBAL_BOT, "address", OWNER, "trust")

    def tearDown(self):
        self.store.close()

    def run_gate(self, body, mode=gate.RESTRICTED, bot="ceo"):
        return gate.evaluate(self.store, bot, mode, body)

    def outcomes(self):
        return [event["outcome"] for event in reversed(self.store.events())]

    def test_unknown_sender_is_held_then_released_on_allow(self, *_):
        self.assertEqual(self.run_gate(payload()), gate.SILENT)
        pending = self.store.pending()
        self.assertEqual([(p["bot"], p["sender"], p["messages"]) for p in pending], [("ceo", "supplier@vendor.example", 1)])
        self.store.set_rule("ceo", "domain", "vendor.example", "allow")
        held_id = self.store.held_for("ceo", "supplier@vendor.example")[0]["id"]
        delivered = json.loads(self.run_gate({"mail_gate": {"release": held_id}}))
        self.assertEqual(delivered["mail"]["subject"], "Invoice 42")
        self.assertEqual(delivered["mail"]["attachments_text"], "a.pdf (application/pdf, 900 bytes)")
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.outcomes(), ["held", "released"])

    def test_release_without_a_decision_keeps_the_mail_held(self, *_):
        self.run_gate(payload())
        held_id = self.store.held_for("ceo", "supplier@vendor.example")[0]["id"]
        self.assertEqual(self.run_gate({"mail_gate": {"release": held_id}}), gate.SILENT)
        self.assertEqual(len(self.store.pending()), 1)

    def test_blocked_address_inside_allowed_domain_is_dropped(self, *_):
        self.store.set_rule("ceo", "domain", "vendor.example", "allow")
        self.store.set_rule("ceo", "address", "supplier@vendor.example", "block")
        self.assertEqual(self.run_gate(payload()), gate.SILENT)
        self.assertEqual(self.outcomes(), ["dropped-blocked"])

    def test_allowed_sender_is_delivered_with_metadata_only_logged(self, *_):
        self.store.set_rule("ceo", "address", "supplier@vendor.example", "allow")
        delivered = json.loads(self.run_gate(payload()))
        self.assertEqual(delivered["mail"]["from"], "supplier@vendor.example")
        event = self.store.events()[0]
        self.assertEqual((event["outcome"], event["subject"], event["attachments"]), ("delivered", "Invoice 42", 1))
        self.assertNotIn("text", event)

    def test_unauthenticated_mail_is_dropped_before_any_rule(self, *_):
        self.store.set_rule("ceo", "domain", "vendor.example", "allow")
        self.assertEqual(self.run_gate(payload(dmarc="fail")), gate.SILENT)
        self.assertEqual(self.outcomes(), ["dropped-unauthenticated"])
        self.assertEqual(self.store.pending(), [])

    def test_forged_owner_never_reaches_either_route(self, *_):
        forged = payload(sender=OWNER, dmarc="fail")
        self.assertEqual(self.run_gate(forged, mode=gate.TRUSTED), gate.SILENT)
        self.assertEqual(self.run_gate(forged), gate.SILENT)
        self.assertEqual(self.outcomes(), ["dropped-unauthenticated"])

    def test_verified_owner_goes_only_to_the_trusted_route(self, *_):
        mine = payload(sender=OWNER, message_id="<own@example.org>")
        self.assertEqual(self.run_gate(mine), gate.SILENT)
        delivered = json.loads(self.run_gate(mine, mode=gate.TRUSTED))
        self.assertEqual(delivered["mail"]["from"], OWNER)
        self.assertEqual(self.outcomes(), ["delivered"])
        self.assertEqual(self.store.events()[0]["route"], gate.TRUSTED)

    def test_webhook_retry_is_processed_once(self, *_):
        self.store.set_rule("ceo", "domain", "vendor.example", "allow")
        self.assertNotEqual(self.run_gate(payload()), gate.SILENT)
        self.assertEqual(self.run_gate(payload()), gate.SILENT)
        self.assertEqual(self.outcomes(), ["delivered"])

    def test_trusted_domain_with_blocked_address(self, *_):
        self.store.set_rule("ceo", "domain", "partner.example", "trust")
        self.store.set_rule("ceo", "address", "intern@partner.example", "block")
        trusted = payload(sender="boss@partner.example", message_id="<t1@partner.example>")
        blocked = payload(sender="intern@partner.example", message_id="<t2@partner.example>")
        self.assertNotEqual(self.run_gate(trusted, mode=gate.TRUSTED), gate.SILENT)
        self.assertEqual(self.run_gate(trusted), gate.SILENT)
        self.assertEqual(self.run_gate(blocked, mode=gate.TRUSTED), gate.SILENT)
        self.assertEqual(self.run_gate(blocked), gate.SILENT)
        self.assertEqual(self.outcomes(), ["delivered", "dropped-blocked"])

    def test_held_mail_released_on_trust_goes_only_to_the_trusted_route(self, *_):
        self.run_gate(payload())
        held_id = self.store.held_for("ceo", "supplier@vendor.example")[0]["id"]
        self.store.set_rule("ceo", "address", "supplier@vendor.example", "trust")
        self.assertEqual(self.run_gate({"mail_gate": {"release": held_id}}), gate.SILENT)
        self.assertEqual(len(self.store.pending()), 1)
        delivered = json.loads(self.run_gate({"mail_gate": {"release": held_id}}, mode=gate.TRUSTED))
        self.assertEqual(delivered["mail"]["from"], "supplier@vendor.example")
        self.assertEqual(self.store.pending(), [])
        self.assertEqual(self.outcomes(), ["held", "released"])

    def test_rules_are_per_bot(self, *_):
        self.store.set_rule("cfo", "domain", "vendor.example", "allow")
        self.assertEqual(self.run_gate(payload(), bot="ceo"), gate.SILENT)
        self.assertEqual(self.outcomes(), ["held"])


if __name__ == "__main__":
    unittest.main()
