from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="DarsAmooz local web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    # Ensure cwd-relative data/ and .env resolve from repo root when possible
    os.environ.setdefault("PYTHONUTF8", "1")

    uvicorn.run(
        "darsamooz.web.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
