# Notifications

The brief always lands in the app: Today shows it, Reports keeps every past one. Push is optional
and there is exactly one channel wired, Telegram, because one that works beats four that half do.

## Telegram

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, answer two questions, copy the
   token into `TELEGRAM_BOT_TOKEN` in `.env`.
2. Send your bot a message, then open `https://api.telegram.org/bot<token>/getUpdates` and read
   `message.chat.id`. A bot cannot start a conversation, so this first message is what makes you
   reachable.
3. Put the id in `TELEGRAM_CHAT_ID` and run `python scripts/bootstrap.py`, or paste it straight into
   Settings, Pipeline, Telegram chat id. It is stored in `intel.config.telegram`.

What arrives each morning:

- a short message: the date, how many setups and risk flags, the run status, the five biggest
  movers, the setups and the risks by name, and any issues the run hit;
- the full brief as a `.md` attachment, unless you turn off **Attach the full brief**.

The split exists because Telegram rejects a message over 4096 characters and a full brief for a
50-ticker watchlist is several times that. The short message is trimmed to 3900 characters, which
leaves room for a long issues line.

Leave the chat id blank and the whole branch is skipped: no credential needed, no error, the brief
just stays in the app.

## Email, if you prefer it

Not shipped, because every SMTP setup is different and an unused mail node is worse than none. The
shape is one node, though. In `workflows/daily.json`, the Telegram branch hangs off **Telegram
enabled?**; add an n8n Send Email node beside it with:

- **To**: your address. **Subject**: `={{ $('Build brief').first().json.subject }}`
- **HTML**: `={{ $('Build brief').first().json.html }}` (the brief renders as a table-based HTML
  document, ready to send)
- an SMTP credential of your own, and `onError: continueRegularOutput` so a mail failure never
  fails the run.

Wire it after **Build brief**, point its output at **Store report + finish run**, and gate it on
`intel.config.notify_email`, which is still read for exactly this reason. Then regenerate the
canvas (`scripts/canvas/zone_layout.py`) and redeploy.

Whatever channel you add, keep the pattern the Telegram branch uses: the run records
`reports.sent_to` only when the send node really executed without an error, so a silent delivery
failure shows up in the data rather than being assumed.

## What is not here

No Slack, no Discord, no webhooks-to-anywhere. They are all the same shape as the email note above,
and n8n has a node for each. Pull requests that add one as an optional, off-by-default branch are
welcome.
