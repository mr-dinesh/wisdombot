#!/usr/bin/env python3
"""Embed the standalone quote units into Vectorize through the deployed Worker.

    INGEST_TOKEN=... python3 -I build/ingest.py https://wisdombot.<you>.workers.dev [corpus/units.jsonl]

Sends batches of 100 to POST /admin/ingest; the Worker embeds them with Workers AI
and upserts into the index. Safe to re-run (upsert by unit ID).
"""
import json
import os
import pathlib
import sys
import time
import urllib.request

def main(base: str, units_path: pathlib.Path) -> None:
    token = os.environ.get("INGEST_TOKEN")
    if not token:
        sys.exit("set INGEST_TOKEN in the environment (same value as the Worker secret)")
    units = [u for u in map(json.loads, units_path.read_text().splitlines()) if u["standalone"]]
    print(f"{len(units)} standalone units")
    for i in range(0, len(units), 100):
        batch = units[i:i + 100]
        req = urllib.request.Request(base.rstrip("/") + "/admin/ingest", data=json.dumps(batch).encode(),
                                     headers={"Authorization": f"Bearer {token}", "content-type": "application/json",
                                              "User-Agent": "wisdombot-ingest/0.1"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    r.read()
                break
            except Exception as e:
                if attempt == 3:
                    raise
                print(f"  batch {i}: {e}; retrying", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
        print(f"  {min(i + 100, len(units))}/{len(units)}")

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    main(sys.argv[1], pathlib.Path(sys.argv[2] if len(sys.argv) == 3 else "corpus/units.jsonl"))
