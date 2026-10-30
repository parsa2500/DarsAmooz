#!/usr/bin/env python3
"""Download Physics 1 (grade 10, math track) PDF for local ingest. Does not commit it."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "manifests" / "physics10-riazi-110209.json"
OUT_DIR = ROOT / "data" / "textbooks"
OUT_PDF = OUT_DIR / "physics10-riazi-110209.pdf"

# Fallback mirrors if the primary URL fails
FALLBACKS = [
    "https://physicfa.ir/wp-content/uploads/2024/12/Physic1R-1403-1404-physicfa.pdf",
    "https://dl.daneshchi.ir/file/chap-school/Physic-Riazi-10_Daneshchi.iR.pdf",
]


def main() -> int:
    meta = json.loads(MANIFEST.read_text(encoding="utf-8"))
    urls = [meta["source_url"], *[u for u in FALLBACKS if u != meta["source_url"]]]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for url in urls:
        try:
            print(f"Downloading: {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "DarsAmooz/0.1"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if len(data) < 500_000:
                raise RuntimeError(f"file too small ({len(data)} bytes)")
            OUT_PDF.write_bytes(data)
            print(f"Saved {OUT_PDF} ({len(data)} bytes)")
            print(f"Official page: {meta['official_page']}")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"Failed: {e}", file=sys.stderr)
            last_err = e
    print(f"All downloads failed. Last error: {last_err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
