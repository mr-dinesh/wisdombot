#!/usr/bin/env python3
"""Parse the raw texts into passages and short quote units.

    python3 -I build/split.py RAW_DIR OUT_DIR

Writes OUT_DIR/passages.jsonl (one full passage per line, used by /source) and
OUT_DIR/units.jsonl (1-2 sentence spans of 8-40 words; what the bot quotes) and
OUT_DIR/public/p/<shard>.json (passages served to the Worker for /source).
Text is cut only at sentence boundaries, never edited, apart from removing
footnote markers and joining wrapped lines.
"""
import html
import html.parser
import json
import pathlib
import re
import sys

MIN_WORDS, MAX_WORDS = 8, 40

# A unit starting with one of these leans on the sentence before it.
DEPENDENT_START = re.compile(
    r"^(For|But|And|Therefore|Thus|Hence|So|This|These|Those|Then|Nor|Or|Yet|"
    r"Wherefore|Accordingly|Again|Besides|Further|Furthermore|Moreover|"
    r"It is this|She|They|Him|His|Her|Their|In this|In that|Such a|Whereas)\b")

# Never quote these: suicide as a way out, and passages that read badly to
# someone in distress. Matched case-insensitively against unit and passage text.
HARMFUL = re.compile(
    r"suicide|self-destruct|kill (thy|your|him|her|one)sel|slay (thy|your)sel|"
    r"take (thy|your|his|one's|their) (own )?life|end (thy|your|his|one's) life|"
    r"depart (from|out of) life|quit (life|it\b)|go out of life|walk out|"
    r"the door is open|door stands open|way out|smoke|release (thee|you|him)|"
    r"open (a|the|thy|your) vein|cut (thy|your) throat|hang (thy|your)sel|"
    r"depart then|leave (life|the world)|free (thy|your)sel(f|ves) from life|"
    r"let them kill|better than to live|better to die|(wish|long)\w* (to die|for death)|"
    r"welcome death|seek death|hasten (thy|your|his) death", re.I)

SENT_END = re.compile(r'(?<=[.!?])["”’\')\]]?\s+(?=["“‘(\[]?[A-Z])')
ABBREV = re.compile(r"\b(Mr|Mrs|Dr|St|c|v|cf|i\.e|e\.g|viz|Epp|ch|vol|p|pp|tr|ed)\.$", re.I)


def words(s: str) -> int:
    return len(s.split())


def sentences(text: str) -> list[str]:
    parts, out = SENT_END.split(text), []
    for p in parts:
        if out and ABBREV.search(out[-1]):
            out[-1] += " " + p
        else:
            out.append(p)
    return [s.strip() for s in out if s.strip()]


def clean(text: str) -> str:
    text = re.sub(r"\[[A-Z]\]", "", text)            # Long's footnote markers
    text = re.sub(r"\[Footnote:[^\]]*\]", "", text)
    # editor's cross-references, e.g. "(viii. 29)", "(ii. 16; iii. 11)", "(See v. 33.)"
    text = re.sub(r"\s*\((?:see |comp\. |cf\. )?[ivx]+\. \d+[^()]{0,40}\)", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.replace(" ,", ",").replace(" .", ".").strip()


def paragraphs(block: str) -> list[str]:
    return [clean(p) for p in re.split(r"\n\s*\n", block) if p.strip()]


# --- Marcus Aurelius (Long) -------------------------------------------------

ROMAN = "I II III IV V VI VII VIII IX X XI XII".split()


def meditations(path: pathlib.Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    end = next(i for i, l in enumerate(lines) if l.strip() == "INDEXES.")
    heads, start = [], 0
    for num in ROMAN:   # find "I." ... "XII." in order, the real book headers
        start = next(i for i in range(start, end) if lines[i].strip() == f"{num}.")
        heads.append(start)
        start += 1
    # Book I is preceded by Long's essay; the true "I." is the last one before "II."
    heads[0] = max(i for i in range(heads[1]) if lines[i].strip() == "I.")
    for b, h in enumerate(heads, 1):
        stop = heads[b] if b < len(heads) else end
        sec, buf = 1, []
        body = "\n".join(lines[h + 1:stop])
        # Long's notes are indented 4 spaces; they are commentary, not Marcus
        body = "\n\n".join(b for b in re.split(r"\n\s*\n", body) if not b.startswith("    "))
        for para in paragraphs(body):
            if re.match(r"^\[[A-Z]\]|^\[Greek", para) and not buf:
                continue
            if re.match(r"^[A-Z]\]|^\[[A-Z]\]", para):   # footnote body
                continue
            m = re.match(r"^(\d+)\. (.*)", para)
            if m:
                if buf:
                    yield f"{b}.{sec}", " ".join(buf)
                sec, buf = int(m.group(1)), [m.group(2)]
            elif not re.match(r"^\S+ [ivx]+\. \d", para):  # skip cross-ref notes
                buf.append(para)
        if buf:
            yield f"{b}.{sec}", " ".join(buf)


# --- Epictetus (Long) -------------------------------------------------------

def epictetus(path: pathlib.Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    sel = next(i for i, l in enumerate(lines) if l.strip() == "A SELECTION FROM THE DISCOURSES OF EPICTETUS." and i > 100)
    ench = max(i for i, l in enumerate(lines) if l.strip() == "THE ENCHEIRIDION, OR MANUAL.")
    end = next(i for i, l in enumerate(lines) if l.startswith("*** END OF THE PROJECT GUTENBERG"))

    # Discourses: chapters open with an UPPER-CASE TITLE.—text
    chap, buf, title = 0, [], ""
    for para in paragraphs("\n".join(lines[sel + 1:ench])):
        if re.match(r"^\[[A-Z]\]", para):
            continue
        m = re.match(r"^([A-Z][A-Z ,;:'’.()\-]+?)\.?(\[Footnote[^\]]*\])?\s*—\s*(.*)", para)
        if m and m.group(1).isupper():
            if buf:
                yield f"D {chap}", title, buf
            chap, title, buf = chap + 1, m.group(1).strip().rstrip(".").capitalize().replace(" god", " God"), [m.group(3)]
        else:
            buf.append(para)
    if buf:
        yield f"D {chap}", title, buf

    # Encheiridion: roman-numbered chapters
    chap, buf = 0, []
    for para in paragraphs("\n".join(lines[ench + 1:end])):
        if re.match(r"^\[[A-Z]\]", para):
            continue
        if re.fullmatch(r"[IVXL]+\.", para):
            if buf:
                yield f"Ench {chap}", "", buf
            chap, buf = chap + 1, []
        else:
            buf.append(para)
    if buf:
        yield f"Ench {chap}", "", buf


# --- Seneca (Gummere, Wikisource HTML) ---------------------------------------

class LetterParser(html.parser.HTMLParser):
    SKIP_CLASSES = ("ws-header", "wst-header", "ws-noexport", "reference", "references",
                    "mw-references-wrap", "pagenum", "wst-center", "reflist", "mw-editsection")
    VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.sections, self.cur = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        skip = tag in ("style", "script", "sup") and "wst-verse" not in cls \
            or any(c in cls for c in self.SKIP_CLASSES)
        if "wst-verse" in cls and re.fullmatch(r"\d+\.", a.get("id") or ""):
            self.cur = [int(a["id"][:-1]), []]
            self.sections.append(self.cur)
            skip = True     # the number itself
        self.stack.append(skip)

    def handle_endtag(self, tag):
        if tag not in self.VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.cur is not None and not any(self.stack):
            self.cur[1].append(data)


def seneca(folder: pathlib.Path):
    for f in sorted(folder.glob("*.json")):
        d = json.loads(f.read_text())
        p = LetterParser()
        p.feed(d["html"])
        for num, chunks in p.sections:
            text = re.sub(r"\s*Farewell\.?\s*$", "", clean(html.unescape("".join(chunks))))  # letter sign-off
            if text:
                yield d["letter"], num, text


# --- units ---------------------------------------------------------------------

def make_units(pid: str, base: dict, text: str, sents=None):
    """sents: optional [(sentence, section)] for Seneca, where pid is the letter."""
    labels = None
    if sents is not None:
        labels = [sec for _, sec in sents]
        sents = [t for t, _ in sents]
    else:
        sents = sentences(text)
    for i in range(len(sents)):
        for j in (i, i + 1):
            if j >= len(sents):
                break
            span = " ".join(sents[i:j + 1])
            n = words(span)
            if not MIN_WORDS <= n <= MAX_WORDS:
                continue
            if "[Greek" in span or "--" in span[:3] or not span[0].isupper() and span[0] not in "\"“‘'":
                continue
            if not re.search(r"[.!][\"”’')\]]?$", span):      # questions and colons need context
                continue
            if re.search(r"\b(says?|said|replied|answered)\b", span) and re.search(r"[\"“”]|--|—", span):
                continue                                      # dialogue fragments
            uid = f"{pid}#{i + 1}" if i == j else f"{pid}#{i + 1}-{j + 1}"
            parent, extra = pid, {}
            if labels:   # Seneca: cite the section the quote starts in
                parent = f"{pid}.{labels[i]}"
                extra = {"ref": parent.split(" ")[-1]}
            yield {**base, **extra, "id": uid, "parent": parent, "text": span, "words": n,
                   "standalone": not DEPENDENT_START.match(span.lstrip("\"“‘'"))}


def main(raw: pathlib.Path, out: pathlib.Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    excluded = {l.split("#")[0].strip() for l in (out / "excluded.txt").read_text().splitlines()
                if l.strip() and not l.startswith("#")} if (out / "excluded.txt").exists() else set()
    passages, units = [], []
    dropped = 0

    def add(pid, base, text):
        nonlocal dropped
        passages.append({**base, "id": pid, "text": text})
        if pid in excluded or pid.rsplit(".", 1)[0] in excluded:
            return
        for u in make_units(pid, base, text):
            if HARMFUL.search(u["text"]):
                dropped += 1
                continue
            units.append(u)

    gm = "https://www.gutenberg.org/ebooks/"
    for ref, text in meditations(raw / "meditations_long.txt"):
        add(f"M {ref}", {"author": "Marcus Aurelius", "work": "Meditations", "ref": ref,
                         "translator": "George Long", "url": gm + "15877"}, text)

    for ref, title, paras in epictetus(raw / "epictetus_long.txt"):
        work = "Encheiridion" if ref.startswith("Ench") else "Discourses"
        for k, para in enumerate(paras, 1):
            r = f"{ref.split()[1]}.{k}"
            add(f"E {ref}.{k}", {"author": "Epictetus", "work": work, "ref": r, "title": title,
                                 "translator": "George Long", "url": gm + "10661"}, para)

    # Gummere's section numbers fall mid-sentence, so units are cut from the whole
    # letter and labelled with the section where they start.
    ws = "https://en.wikisource.org/wiki/Moral_letters_to_Lucilius/Letter_"
    letters = {}
    for letter, sec, text in seneca(raw / "seneca_letters"):
        letters.setdefault(letter, []).append((sec, text))
    for letter, secs in letters.items():
        base = {"author": "Seneca", "work": "Letters", "translator": "R. M. Gummere", "url": f"{ws}{letter}"}
        whole, starts = "", []
        for sec, text in secs:
            passages.append({**base, "id": f"S Ep {letter}.{sec}", "ref": f"{letter}.{sec}", "text": text})
            starts.append((len(whole), sec))
            whole += text + " "
        if f"S Ep {letter}" in excluded:
            continue
        pos = 0
        sents = []
        for snt in sentences(whole):
            at = whole.find(snt, pos)
            pos = at + len(snt)
            sec = max(s for off, s in starts if off <= max(at, 0))
            sents.append((snt, sec))
        for u in make_units(f"S Ep {letter}", base, " ".join(t for t, _ in sents), sents=sents):
            if u["parent"] in excluded or HARMFUL.search(u["text"]):
                dropped += 1
                continue
            units.append(u)

    with open(out / "passages.jsonl", "w") as f:
        for p in passages:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(out / "units.jsonl", "w") as f:
        for u in units:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    # Full passages for /source, one static file per shard (matches shardOf() in worker.js)
    shards = {}
    for p in passages:
        a, b = p["id"].split(" ")[:2]
        key = f"M-{b.split('.')[0]}" if a == "M" else f"S-{p['id'].split(' ')[2].split('.')[0]}" if a == "S" else f"E-{b}"
        shards.setdefault(key, {})[p["id"]] = p
    pub = out / "public" / "p"
    pub.mkdir(parents=True, exist_ok=True)
    for key, d in shards.items():
        (pub / f"{key}.json").write_text(json.dumps(d, ensure_ascii=False))
    by = {}
    for u in units:
        by[u["author"]] = by.get(u["author"], 0) + 1
    print(f"passages {len(passages)}  units {len(units)}  standalone "
          f"{sum(u['standalone'] for u in units)}  dropped-harmful {dropped}  by author {by}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
