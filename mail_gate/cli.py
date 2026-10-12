import json
from typing import Any

from mail_gate.release import ACTIONS, SCOPES, apply_rule
from mail_gate.routes import sync
from mail_gate.store import GLOBAL_BOT, Store


def _setup(parser: Any) -> None:
    commands = parser.add_subparsers(dest="mail_gate_command")
    commands.add_parser("sync", help="Write the gate wrappers and webhook routes for every bot with a mailbox")
    commands.add_parser("status", help="Pending senders and today's counters")
    rule = commands.add_parser("rule", help="Add or change a rule (held mail it covers is re-evaluated)")
    rule.add_argument("action", choices=ACTIONS)
    rule.add_argument("scope", choices=SCOPES)
    rule.add_argument("value", help="Full address for an address rule, bare domain for a domain rule")
    rule.add_argument("--bot", default=GLOBAL_BOT, help="Bot profile name; '*' (default) applies to every bot")
    parser.set_defaults(func=_run)


def _run(args: Any) -> None:
    command = getattr(args, "mail_gate_command", None)
    if command == "sync":
        result: Any = sync()
    elif command == "rule":
        store = Store()
        try:
            result = apply_rule(store, args.bot, args.scope, args.value, args.action)
        finally:
            store.close()
    elif command == "status":
        store = Store()
        try:
            result = {"pending": store.pending(), "today": store.counters("day", 1)}
        finally:
            store.close()
    else:
        result = {"usage": "hermes mail-gate {sync,status,rule}"}
    print(json.dumps(result, indent=2, default=str))


def register_cli(ctx: Any) -> None:
    ctx.register_cli_command(name="mail-gate", help="Mail gate rules, routes and status", setup_fn=_setup,
                             handler_fn=_run)
