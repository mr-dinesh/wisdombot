#!/usr/bin/env python3
"""Download the public-domain source texts into a raw directory.

    python3 build/fetch.py RAW_DIR

- Marcus Aurelius, Meditations, tr. George Long (1862)   Gutenberg #15877
- Epictetus, Discourses (selection) + Encheiridion, tr. George Long   Gutenberg #10661
- Seneca, Moral Letters to Lucilius 1-124, tr. R. M. Gummere (1917-25)   Wikisource

Downloads are kept as-is; split.py does all parsing. Re-running skips files
already present.
"""
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.error
import urllib.request

UA = "wisdombot-build/0.1 (personal, non-commercial)"
GUTENBERG = {"meditations_long": 15877, "epictetus_long": 10661}
WIKISOURCE_API = "https://en.wikisource.org/w/api.php"


def get(url: str) -> bytes:
    """GET with backoff: Wikisource answers 429 when asked too fast."""
    for attempt in range(6):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            wait = int(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1)
            print(f"  429, waiting {wait}s", file=sys.stderr)
            time.sleep(wait)


def main(raw: pathlib.Path) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    for name, gid in GUTENBERG.items():
        out = raw / f"{name}.txt"
        if not out.exists():
            out.write_bytes(get(f"https://www.gutenberg.org/cache/epub/{gid}/pg{gid}.txt"))
            print("got", out.name)

    letters = raw / "seneca_letters"
    letters.mkdir(exist_ok=True)
    for n in range(1, 125):
        out = letters / f"{n:03d}.json"
        if out.exists():
            continue
        q = urllib.parse.urlencode({
            "action": "parse", "page": f"Moral letters to Lucilius/Letter {n}",
            "prop": "text", "format": "json", "formatversion": 2, "redirects": 1,
        })
        data = json.loads(get(f"{WIKISOURCE_API}?{q}"))
        if "error" in data:
            print(f"letter {n}: {data['error'].get('info')}", file=sys.stderr)
            continue
        out.write_text(json.dumps({"letter": n, "html": data["parse"]["text"]}))
        print("got letter", n)
        time.sleep(4)  # be polite to Wikisource


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(pathlib.Path(sys.argv[1]))
