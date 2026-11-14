// TheWisdomBot — a private Telegram bot that answers a real-life problem with one
// short Stoic quote (verbatim, from a public-domain translation) and a short "Do:".
//
// Routes
//   POST /tg            Telegram webhook (secret header + allowed user ID)
//   GET  /health        Binding check for model-watch; sends nothing to Telegram
//   POST /admin/ingest  Bearer INGEST_TOKEN; embeds quote units into Vectorize
//
// The model never writes quote text. It picks one candidate ID; the Worker prints
// that candidate's stored text. See DESIGN.md §1.

const EMBED_MODEL = "@cf/baai/bge-small-en-v1.5";            // 384 dims
const CHAT_MODELS = [
  "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
  "@cf/mistralai/mistral-small-3.1-24b-instruct",
];
const TOP_K = 20;
const PER_AUTHOR = 7;
const STATE_TTL = 3600;   // /more and /source remember the last question for 1 h

const CRISIS = /\b(suicid\w*|kill (my ?self|myself)|end (it all|my life)|want to die|don'?t want to (live|be alive)|self[- ]?harm|cut(ting)? myself|no reason to live|better off dead|take my (own )?life)\b/i;
const CRISIS_REPLY =
  "I'm not going to answer this one with a quote.\n\n" +
  "If you are thinking about harming yourself, please talk to someone now. " +
  "Tele-MANAS is free and open 24×7: call <b>14416</b> or <b>1-800-891-4416</b>.\n\n" +
  "And please tell someone you trust, today.";
const NUDGE = "Ask me about something you're facing.";
const HELP =
  "Tell me what you're dealing with, in your own words. I'll answer with one short line " +
  "from Marcus Aurelius, Seneca or Epictetus and one thing to do.\n\n" +
  "/more — another quote for the same problem\n" +
  "/source — the full passage behind the last quote";

const SCHEMA = {
  type: "object",
  properties: {
    is_question: { type: "boolean" },
    crisis: { type: "boolean" },
    pick: { type: "string" },
    do: { type: "string" },
  },
  required: ["is_question", "crisis", "pick", "do"],
};

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    try {
      if (url.pathname === "/tg" && request.method === "POST") return await webhook(request, env, ctx);
      if (url.pathname === "/health") return await health(env);
      if (url.pathname === "/admin/ingest" && request.method === "POST") return await ingest(request, env);
    } catch (err) {
      console.error("wisdombot:", err.stack || err.message);
      return new Response("error", { status: 500 });
    }
    return new Response("not found", { status: 404 });
  },
};

// --- Telegram ----------------------------------------------------------------

async function webhook(request, env, ctx) {
  if (!env.TELEGRAM_WEBHOOK_SECRET ||
      !safeEqual(request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "", env.TELEGRAM_WEBHOOK_SECRET)) {
    return new Response("forbidden", { status: 403 });
  }
  const update = await request.json();
  const msg = update.message;
  const allowed = String(env.ALLOWED_USER_IDS || "").split(",").map(s => s.trim()).filter(Boolean);
  // Answer 200 to everything else so Telegram doesn't retry; ignore strangers silently.
  if (!msg || !msg.text || !allowed.includes(String(msg.from?.id))) return new Response("ok");
  ctx.waitUntil(handle(env, msg).catch(err => {
    console.error("wisdombot handle:", err.stack || err.message);   // never logs message text
    return send(env, msg.chat.id, "Something went wrong on my side. Try again in a minute.");
  }));
  return new Response("ok");
}

async function handle(env, msg) {
  const chat = msg.chat.id;
  const text = msg.text.trim();
  const cmd = text.split(/\s+/)[0].toLowerCase().replace(/@\w+$/, "");

  if (cmd === "/start" || cmd === "/help") return send(env, chat, HELP);
  if (cmd === "/source") return source(env, chat, msg.from.id, text.slice(cmd.length).trim());
  if (cmd === "/more") {
    const st = await getState(env, msg.from.id);
    if (!st) return send(env, chat, "Tell me what's on your mind first.");
    return answer(env, chat, msg.from.id, st.q, st.shown);
  }
  if (cmd.startsWith("/")) return send(env, chat, HELP);
  if (CRISIS.test(text)) return send(env, chat, CRISIS_REPLY);
  return answer(env, chat, msg.from.id, text, []);
}

async function answer(env, chat, uid, q, shown) {
  await tg(env, "sendChatAction", { chat_id: chat, action: "typing" });
  const cands = (await search(env, q)).filter(c => !shown.includes(c.id));
  if (!cands.length) return send(env, chat, "I've run out of fitting quotes for this one. Try putting it another way.");

  const out = await choose(env, q, cands);
  if (out?.crisis) return send(env, chat, CRISIS_REPLY);
  if (out && out.is_question === false && !shown.length) return send(env, chat, NUDGE);

  const pick = cands.find(c => c.id === out?.pick) || cands[0];   // never trust an ID outside the list
  const doLine = out?.pick === pick.id ? trimDo(out.do) : "";
  await putState(env, uid, { q, shown: [...shown, pick.id], last: pick.id });
  return send(env, chat, render(pick, doLine));
}

async function source(env, chat, uid, arg) {
  let id = arg;
  if (!id) {
    const st = await getState(env, uid);
    if (!st?.last) return send(env, chat, "No quote yet. Ask me something first.");
    id = st.last;
  }
  const parent = id.split("#")[0].trim();
  const p = await passage(env, parent);
  if (!p) return send(env, chat, `I don't have a passage called ${esc(parent)}.`);
  return send(env, chat,
    `<b>${esc(cite(p))}</b>\n\n${esc(p.text).slice(0, 3500)}\n\n<a href="${esc(p.url)}">Read it in context</a>`);
}

// --- retrieval and choice -------------------------------------------------------

async function embed(env, texts) {
  const r = await env.AI.run(EMBED_MODEL, { text: texts });
  return r.data;
}

// Seneca's Letters are ~3/4 of the corpus, so cap each author's share of the
// candidates; otherwise Marcus and Epictetus rarely get a look in.
async function search(env, q) {
  const [vec] = await embed(env, [q]);
  const r = await env.VECTORIZE.query(vec, { topK: 50, returnMetadata: "all" });
  const per = {}, out = [];
  for (const m of r.matches) {
    const a = m.metadata?.author;
    if ((per[a] = (per[a] || 0) + 1) <= PER_AUTHOR) out.push({ id: m.id, ...m.metadata });
  }
  return out.slice(0, TOP_K);
}

async function choose(env, q, cands) {
  const list = cands.map((c, i) => `[${i + 1}] ${c.author}: ${c.text}`).join("\n");
  const prompt =
    `A person wrote to you:\n"""${q.slice(0, 1500)}"""\n\n` +
    `Candidate quotes (Stoic, public-domain translations):\n${list}\n\n` +
    `Reply in JSON:\n` +
    `- is_question: true if they describe a problem, worry, decision or ask for guidance; ` +
    `false for small talk, thanks, greetings or acknowledgements.\n` +
    `- crisis: true if they mention wanting to die, suicide or self-harm.\n` +
    `- pick: the number (as a string, e.g. "3") of the ONE quote that best speaks to their situation. ` +
    `Prefer a quote whose meaning is clear on its own. "" if is_question is false.\n` +
    `- do: one or two short, plain sentences (under 35 words) with a concrete thing they can do today ` +
    `in this situation. Don't quote or restate the quote, don't preach, no "Stoics say". ` +
    `For medical, legal or money matters, add that they should get a professional's advice. ` +
    `"" if is_question is false.`;

  for (const model of CHAT_MODELS) {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const r = await env.AI.run(model, {
          messages: [{ role: "user", content: prompt }],
          temperature: 0.3,
          max_tokens: 200,
          response_format: { type: "json_schema", json_schema: SCHEMA },
        });
        const out = parse(r?.response);
        if (!out || typeof out.is_question !== "boolean") continue;
        if (!out.is_question || out.crisis) return out;
        const n = parseInt(String(out.pick).replace(/\D/g, ""), 10);
        if (n >= 1 && n <= cands.length) return { ...out, pick: cands[n - 1].id };
      } catch (err) {
        console.warn("wisdombot model:", model, err.message);
        break;   // try the next model
      }
    }
  }
  return null;   // caller falls back to the top search result, no Do line
}

function parse(resp) {
  if (resp && typeof resp === "object") return resp;
  try { return JSON.parse(String(resp).match(/\{[\s\S]*\}/)?.[0] || ""); } catch { return null; }
}

function trimDo(s) {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  const sents = t.match(/[^.!?]+[.!?]+/g) || (t ? [t] : []);
  return sents.slice(0, 2).join("").trim();
}

// --- rendering --------------------------------------------------------------------

function cite(c) {
  const tr = c.translator ? ` (tr. ${c.translator.split(" ").pop()})` : "";
  if (c.author === "Epictetus" && c.work === "Discourses")
    return `Epictetus, Discourses, “${c.title}”${tr}`;
  return `${c.author}, ${c.work} ${c.ref}${tr}`;
}

function render(c, doLine) {
  let s = `“${esc(c.text)}”\n— ${esc(cite(c))}`;
  if (doLine) s += `\n\n<b>Do:</b> ${esc(doLine)}`;
  return s;
}

const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

async function send(env, chat, text) {
  return tg(env, "sendMessage", { chat_id: chat, text, parse_mode: "HTML", disable_web_page_preview: true });
}

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) console.warn("wisdombot telegram:", method, r.status);
  return r;
}

// --- passages (static assets), state (KV) ------------------------------------------

// Passages are sharded into static files by work: M-5.json, S-13.json, E-D.json, E-Ench.json
function shardOf(id) {
  const [a, b] = id.split(" ");
  if (a === "M") return `M-${b.split(".")[0]}`;
  if (a === "S") return `S-${id.split(" ")[2].split(".")[0]}`;
  if (a === "E") return `E-${b}`;
  return null;
}

async function passage(env, id) {
  const shard = shardOf(id);
  if (!shard || !/^[A-Za-z]-[A-Za-z0-9]+$/.test(shard)) return null;
  const r = await env.ASSETS.fetch(new Request(`https://assets/p/${shard}.json`));
  if (!r.ok) return null;
  const all = await r.json();
  return all[id] || null;
}

async function getState(env, uid) {
  return env.STATE.get(`last:${uid}`, "json");
}
async function putState(env, uid, st) {
  return env.STATE.put(`last:${uid}`, JSON.stringify(st), { expirationTtl: STATE_TTL });
}

// --- ops ------------------------------------------------------------------------------

async function health(env) {
  const checks = {
    ai: !!env.AI, vectorize: !!env.VECTORIZE, state: !!env.STATE, assets: !!env.ASSETS,
    token: !!env.TELEGRAM_BOT_TOKEN, secret: !!env.TELEGRAM_WEBHOOK_SECRET, allowed: !!env.ALLOWED_USER_IDS,
  };
  try { const d = await env.VECTORIZE.describe(); checks.index = d.vectorCount ?? d.vectorsCount ?? 0; }
  catch { checks.index = 0; }   // an empty or missing index is unhealthy
  const ok = Object.values(checks).every(Boolean);
  return Response.json({ ok, checks }, { status: ok ? 200 : 503 });
}

async function ingest(request, env) {
  if (!env.INGEST_TOKEN || !safeEqual(request.headers.get("Authorization") || "", `Bearer ${env.INGEST_TOKEN}`))
    return new Response("forbidden", { status: 403 });
  const units = await request.json();   // [{id, author, work, ref, title?, translator, parent, text}]
  if (!Array.isArray(units) || !units.length || units.length > 100) return new Response("1-100 units", { status: 400 });
  const vecs = await embed(env, units.map(u => u.text));
  const keep = ["author", "work", "ref", "title", "translator", "parent", "text", "url"];
  await env.VECTORIZE.upsert(units.map((u, i) => ({
    id: u.id,
    values: vecs[i],
    metadata: Object.fromEntries(keep.filter(k => u[k] != null).map(k => [k, u[k]])),
  })));
  return Response.json({ upserted: units.length });
}

function safeEqual(a, b) {
  const x = new TextEncoder().encode(a), y = new TextEncoder().encode(b);
  if (x.length !== y.length) return false;
  let d = 0;
  for (let i = 0; i < x.length; i++) d |= x[i] ^ y[i];
  return d === 0;
}
