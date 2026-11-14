# TheWisdomBot

A private Telegram bot. Tell it a problem you're facing, and it replies with one short line from Marcus Aurelius, Seneca or Epictetus, plus one thing to do. Every quote is printed word for word from a public-domain translation, with its citation. The model only chooses which quote to use. It never writes one.

Design and the reasons behind it: [DESIGN.md](DESIGN.md).

## Build the corpus

```bash
python3 -I build/fetch.py /tmp/wisdom-raw        # Gutenberg + Wikisource; Wikisource rate-limits, ~15 min
python3 -I build/split.py /tmp/wisdom-raw corpus  # passages.jsonl, units.jsonl, public/p/*.json
```

`corpus/excluded.txt` lists passages that are never quoted, mainly ones that present suicide as a way out. `split.py` also drops units that match its `HARMFUL` pattern.

## Deploy (once)

```bash
wrangler vectorize create wisdombot-stoic --dimensions=384 --metric=cosine
wrangler kv namespace create STATE          # paste the id into wrangler.toml
wrangler secret put TELEGRAM_BOT_TOKEN      # from @BotFather
wrangler secret put TELEGRAM_WEBHOOK_SECRET # openssl rand -hex 32
wrangler secret put ALLOWED_USER_IDS        # your numeric Telegram user ID
wrangler secret put INGEST_TOKEN            # openssl rand -hex 32
wrangler deploy

INGEST_TOKEN=... python3 -I build/ingest.py https://wisdombot.<account>.workers.dev
curl https://wisdombot.<account>.workers.dev/health

# point Telegram at the Worker (run in your own shell so the token stays out of logs)
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -d url=https://wisdombot.<account>.workers.dev/tg -d secret_token=$TELEGRAM_WEBHOOK_SECRET \
  -d 'allowed_updates=["message"]'
```

## Use

- Any problem or question: one quote and a `Do:` line.
- Small talk ("thanks", "hi"): one line, no quote.
- `/more`: another quote for the same problem.
- `/source`: the full passage behind the last quote.
- If a message mentions suicide or self-harm, the bot sends the Tele-MANAS helpline (14416) and no quote.
