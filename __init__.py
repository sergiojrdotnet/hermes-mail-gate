"""Hermes Mail Gate plugin: mailbox tools for each bot and the ``hermes mail-gate`` command."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mail_gate.cli import register_cli  # noqa: E402
from mail_gate.tools import register_tools  # noqa: E402


def register(ctx):
    register_tools(ctx)
    register_cli(ctx)
