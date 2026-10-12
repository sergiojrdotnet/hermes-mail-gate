# Hermes Mail Gate

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that puts a deterministic gate between a bot's
mailbox and the bot. Every inbound email is judged by a script before any model reads it:

1. **Authentication.** The receiving provider's own SPF/DKIM/DMARC verdicts decide whether the sender is real. A
   DMARC pass is required; a sender that publishes no DMARC policy needs an SPF or DKIM pass aligned with its From
   domain. Anything else is dropped.
2. **Rules, per bot.** `allow`, `block` or `trust`, on an exact address or a whole domain, for one bot or for every
   bot (`*`). The most specific rule wins: the bot's address rule, the bot's domain rule, then the same for every bot.
3. **Outcome.**
   - `trust` wakes the bot on the *trusted* route with its normal tools.
   - `allow` wakes it on the *restricted* route: only the board and its own mailbox (`no_mcp` also drops every MCP
     connector from that run).
   - `block` drops the message silently.
   - No rule: the message is held and the sender waits for a decision in the Mail Gate page.

Decisions re-run the held mail through the gate. The gate logs metadata only (sender, subject, attachment count,
authentication result, outcome) and keeps it for 90 days; bodies are never logged.

It is built for [Forward Email](https://forwardemail.net): each bot address keeps its IMAP mailbox and also lists two
webhook recipients, one per route. Forward Email signs webhooks with `X-Webhook-Signature`, which Hermes's generic
webhook signature check accepts.

## Pieces

| Part | What it does |
| --- | --- |
| `mail_gate/gate.py` | The route script (stdin: Forward Email's JSON; stdout: the decision). |
| `mail_gate/tools.py` | Toolset `mail_gate`: `mail_read`, `mail_save_attachment`, `mail_send`, `mail_move_to_junk`, `mail_gate_pending`. |
| `mail_gate/routes.py` | `hermes mail-gate sync`: per-bot wrapper scripts and the two webhook routes per bot. |
| `dashboard/` | Backend API plus the dashboard tab. |
| `desktop/plugin.js` | Desktop page, a status-bar counter and an OS notification when a new sender waits. |
| `ui/` | The shared React app; `python3 build.py` inlines it into both front ends. |

Attachments never travel through the webhook (`?attachments=false&raw=false`) or the model: the bot saves the ones it
needs into its outbox with `mail_save_attachment`, and `mail_send` only attaches files from that outbox.

## Setup

1. Install and enable the plugin in the default profile and in every bot profile that has a mailbox
   (`EMAIL_ADDRESS` and `EMAIL_PASSWORD` in the profile `.env`):
   `hermes -p <bot> plugins install https://github.com/sergiojrdotnet/hermes-mail-gate --enable --yes-deps`.
2. In the default profile `.env`, set `MAIL_GATE_WEBHOOK_SECRET` (the domain's webhook signature key from Forward
   Email) and `MAIL_GATE_PUBLIC_BASE` (the public origin that reaches the gateway's webhook listener).
3. Run `hermes mail-gate sync` in the default profile. It prints the two webhook URLs per bot; add both as recipients
   of the bot's alias, keeping IMAP storage on.
4. Add a first trust rule, for example `hermes mail-gate rule trust address you@example.com`.
5. Desktop: install the plugin from Git and enable it under Capabilities → Plugins.

## Development

```sh
python3 -m unittest discover -s tests -t .
python3 build.py
```

## License

MIT
