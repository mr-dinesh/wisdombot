# TheWisdomBot — design

A private Telegram bot, for one user (me), in English. You describe a real problem in plain words ("my manager took credit for my work", "I can't stop worrying about my son's exams"). It replies with **one short quote (1–2 lines)** from Marcus Aurelius, Seneca or Epictetus that fits the problem, with an exact citation, and **a short "what to do"** (one or two sentences).

Date: 2026-11-14. Status: building. See §10 for what changed from the first draft.

---

## 1. The hard problem: invented quotes

LLMs routinely make up Stoic quotes or misattribute them. "We suffer more in imagination than in reality" is a loose paraphrase. Many of the "Marcus Aurelius" quotes on the internet aren't his at all. If the model writes the quote text itself, some answers will be fake, and you won't be able to tell which.

**Rule: the model never writes quote text.** It only picks passage IDs from a verified corpus. The Worker then looks up those IDs and prints the stored text word for word, with book and section and the translator's name. If the model returns an ID that isn't in the candidate list, the Worker drops it. So every quote on screen is real, can be traced, and is in the public domain.

## 2. Corpus (public-domain translations)

| Author | Work | Translation | Source | Unit = one passage |
|---|---|---|---|---|
| Marcus Aurelius | Meditations | George Long (1862) | Project Gutenberg | book.section, e.g. `M 5.1` |
| Seneca | Moral Letters to Lucilius | R. M. Gummere (1917–25) | Wikisource | letter.paragraph, e.g. `S Ep 13.4` |
| Epictetus | Enchiridion | George Long / Elizabeth Carter | Project Gutenberg | chapter, e.g. `E Ench 5` |
| Epictetus | Discourses | George Long (1877) | Project Gutenberg | book.chapter.para |

Confirm the exact Gutenberg/Wikisource IDs and copyright status at build time. Leave out 20th-century translations like Hays, Robin Campbell and Basore-era Loebs unless they're confirmed public domain. Musonius Rufus (Lutz, 1947) is probably still in copyright, so it's out for v1.

Size estimate: about 3,000–4,000 passages.

**Quote units.** Replies show only 1–2 lines, so search and selection work on short spans, not whole passages. The build step splits each passage into sentences. It then makes a quote unit from every single sentence and every pair of adjacent sentences, keeping only units of **8–40 words**. Each unit keeps its parent ID: `M 5.16#1` is sentence 1 of Meditations 5.16, and `M 5.16#1-2` is sentences 1–2. The text is cut only at sentence boundaries and never edited, so a quote is always the translator's exact words. Expect about 25,000 units, well inside the Vectorize free tier.

A sentence that only makes sense in context (it starts with "This", "For", "But", "Therefore", or is a bare fragment) gets marked `standalone:false` at tagging time and is never shown alone. This stops quotes that read well but mean something different out of context.

**Excluded on purpose:** passages that recommend or accept suicide as a way out. Seneca says this in Letters 12, 24, 58, 70, 77 and 91 in places, and so do Meditations 5.29 and 8.47 and Epictetus' "the door is open". Someone asking for help with a hard situation must never get one of these. Keep the excluded IDs in `corpus/excluded.txt` with a reason for each, so the list can be reviewed.

Each quote-unit record (the full passage is stored separately under its parent ID, for `/source`):
```json
{"id":"M 5.16#1","parent":"M 5.16","author":"Marcus Aurelius","work":"Meditations",
 "ref":"5.16","translator":"George Long",
 "text":"Such as are thy habitual thoughts, such also will be the character of thy mind; for the soul is dyed by the thoughts.",
 "words":22,"standalone":true,
 "url":"https://www.gutenberg.org/ebooks/15877"}
```

## 3. Architecture

Same stack as hn_blackout, juicesec and argus: **one Cloudflare Worker**, free tier, no server to run.

```
Telegram ──webhook──▶ Worker  /tg
                        │ 1. check secret header + allowed user ID
                        │ 2. embed query      (Workers AI  @cf/baai/bge-small-en-v1.5, 384 dims)
                        │ 3. top 20 quote units (Vectorize, filter standalone=true)
                        │ 4. choose + explain (Workers AI  llama-3.3-70b-fp8-fast,
                        │                      fallback mistral-small-3.1-24b)
                        │ 5. validate IDs ⊂ candidates; fetch text from KV
                        └▶ sendMessage (HTML parse mode)
```

- **Vectorize** holds one vector per quote unit, with metadata `{parent, author, work, ref, translator, text, url}`. Only standalone units are indexed.
- **Static assets** `corpus/public/p/<shard>.json` hold the full passages for `/source`. This uses no KV writes; the free plan allows only 1,000 a day.
- **Build step** (`build/`, Python, run locally once): `fetch.py` downloads the texts → `split.py` makes passages, units and shards → `ingest.py` posts units to the Worker's `/admin/ingest`, which embeds them and upserts them into Vectorize.

### Prompt contract (step 4)
Input: your message plus the 20 candidates (ID, author, text). Output must be JSON:
```json
{"is_question":true,
 "pick":"M 5.16#1",
 "do":"one or two plain sentences: a concrete next step for this situation",
 "crisis":false}
```
- `is_question:false` for small talk or acknowledgements. Then there is no pick, and the bot sends the one-line nudge. **The bot sends quotes only when you ask a question or describe a problem.** It sends nothing on its own: no daily push and no cron.
- Exactly one pick.
- `do` has at most 2 sentences and about 35 words. It must be practical: something you can do today, not a sermon. It must not quote or paraphrase the quote. The Worker cuts it at 2 sentences if the model runs long.
- If the JSON is invalid or the pick isn't one of the candidates, retry once. If that also fails, send the top search result with no `do` line.

### Reply format
Three lines, nothing more:
```
"Such as are thy habitual thoughts, such also will be the character of thy mind; for the soul is dyed by the thoughts."
— Marcus Aurelius, Meditations 5.16 (tr. Long)

Do: Tonight, write down the one worry you replay most, and next to it the one step you control.
```
No preamble, no restating your problem, and no command menu. The commands are listed in `/help`.

## 4. Commands

| Command | Does |
|---|---|
| a question or problem | Main flow above |
| anything else ("thanks", "ok", "hi") | One short line, no quote: "Ask me about something you're facing." |
| `/more` | One different quote and `Do` for your last question, skipping quotes already shown (last query kept 1 h in KV) |
| `/source` | The full passage behind the last quote, with the Gutenberg link (or `/source M 5.16` for any passage) |
| `/help` | Usage |

Voice notes are out for v1. Add them later via Workers AI Whisper if you want them.

## 5. Safety and privacy

- **Private:** `ALLOWED_USER_IDS` secret, with your Telegram ID only. Anyone else gets no reply.
- **Webhook auth:** `setWebhook` with `secret_token`. The Worker rejects requests whose `X-Telegram-Bot-Api-Secret-Token` doesn't match (constant-time compare).
- **No logs of your problems:** never log message text. Only the last query is stored, in KV with a 1 h TTL, for `/more`. Workers AI doesn't train on inputs.
- **Crisis check:** the model sets `crisis:true`, and a keyword check runs as a backstop (self-harm, suicide, "end it"…). If either fires, the bot sends **no Stoic passages**. It sends a short, kind message with Tele-MANAS **14416** / **1-800-891-4416** (India, 24×7) and asks you to reach someone you trust.
- **No advice beyond the texts:** for medical, legal or money matters, the `Do` line says to get a professional opinion. The bot does not act as a counsellor.

## 6. Secrets and config

| Name | Kind | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | secret | from @BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | secret | `openssl rand -hex 32` |
| `ALLOWED_USER_IDS` | secret | your Telegram user ID |
| `INGEST_TOKEN` | secret | guards `/admin/ingest` |
| `AI`, `VECTORIZE`, `ASSETS`, `STATE` | bindings | `wrangler.toml` |

Set these with `wrangler secret put`, never in chat or files.

**Bot username:** Telegram usernames must end in `bot`. `TheWisdomBot` is valid but probably taken. Fallbacks: `TheStoicWisdomBot`, `WisdomOfStoaBot`. The display name can still be "TheWisdomBot".

## 7. Layout

```
wisdombot/
  DESIGN.md
  build/        fetch.py  split.py  ingest.py
  corpus/       excluded.txt; passages.jsonl, units.jsonl, public/p/*.json (generated, committed)
  worker.js     webhook, commands, retrieval, rendering
  wrangler.toml
  README.md
```

Housekeeping: add both models to `model-watch/watch.json`, add a row to CLAUDE.md, and add a probe that calls the Worker's `GET /health` route (checks bindings, sends nothing to Telegram).

## 8. Build order

1. Corpus: fetch, split, review exclusions. **Checkpoint: read about 30 random passages to check splits and boilerplate.**
2. Embeddings uploaded to Vectorize through `/admin/ingest`.
3. Worker main flow, tested with `wrangler dev` and curl using fake Telegram updates.
4. Evaluation set: 25 real-life prompts (work, family, anger, grief, money, health, fear of failure, insult, procrastination). For each, judge whether the quote fits, whether it reads correctly on its own, and whether `Do` is practical and short. Also make sure no excluded ID ever shows up, and that the crisis prompts never return passages.
5. BotFather, deploy, setWebhook, restrict to your ID.
6. `/more`, `/source`.

## 9. Decisions (2026-11-14)

- **Users:** just me.
- **Language:** English only.
- **When it replies with quotes:** only when I ask a question or describe a problem. No daily passage, no cron, no `/random`.
- **Reply size:** one quote of 1–2 lines, plus a `Do:` line of at most 2 sentences.

Still open: should more Stoics be added later (Cicero's *Tusculan Disputations*, Musonius), even if the translations aren't as good?

## 10. Changes made while building (2026-11-14)

- **Meditations:** Gutenberg #2680 turned out to be Casaubon's 1634 translation. The corpus uses Long's version, #15877. Long's translator notes (indented) and cross-references like "(viii. 29)" are removed.
- **Seneca:** the Stewart essays are dropped for v1; the Letters cover the ground. Gummere's section numbers fall mid-sentence, so units are cut from the whole letter and cite the section where they start.
- **Embeddings:** bge-small (384 dims) instead of bge-base (768). About 10,000 vectors × 768 would exceed the free plan's 5 million stored dimensions.
- **No theme tags:** an LLM tagging pass over every unit would cost more than the free daily Workers AI allowance. Search plus the model's pick do the job. The "is this standalone?" check is a rule (it rejects quotes starting with "For", "But", "This" and similar, questions, and dialogue fragments) instead of an LLM.
- **Meditations Book 1** is excluded: it's a list of thanks to teachers and family, not advice.
- **Specific `Do:` lines (later the same day):** the first prompt gave generic advice ("talk to your brother calmly", "seek professional advice"). The prompt now has a system role, a `situation` field that restates the facts before `do` is written, rules (use a detail from the message, be exact, be kind to others involved, no stock phrases, a professional only for medical or legal matters) and one bad/good example. `POST /admin/try` (Bearer `INGEST_TOKEN`) runs the flow without Telegram, for testing prompts.
