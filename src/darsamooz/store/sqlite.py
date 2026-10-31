from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from darsamooz.config import Settings, get_settings
from darsamooz.sections import load_sections, sections_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sections (
    book_slug TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    section_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    order_idx INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (book_slug, chapter, section_id)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    chapter INTEGER,
    path TEXT,
    finished_at TEXT,
    question_count INTEGER
);

CREATE TABLE IF NOT EXISTS run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    status TEXT,
    section_id TEXT,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_run_items_run ON run_items(run_id);
CREATE INDEX IF NOT EXISTS idx_sections_chapter ON sections(book_slug, chapter);
"""


def default_db_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return Path(settings.data_dir) / "darsamooz.sqlite"


def connect(db_path: Path | None = None, settings: Settings | None = None) -> sqlite3.Connection:
    path = db_path or default_db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def sync_sections(
    conn: sqlite3.Connection,
    book_slug: str = "physics10-riazi-110209",
    chapters: list[int] | None = None,
    settings: Settings | None = None,
) -> int:
    """Replace sections rows for the given book/chapters from sections.json."""
    settings = settings or get_settings()
    from darsamooz.ingest import PHYSICS10_CHAPTERS

    nums = chapters or [n for n, _ in PHYSICS10_CHAPTERS]
    total = 0
    for n in nums:
        path = sections_path(book_slug, n, settings)
        if not path.is_file():
            # Try rebuild via load_sections
            sections = load_sections(book_slug, n, settings)
        else:
            sections = load_sections(book_slug, n, settings, rebuild_if_missing=False)
            if not sections:
                sections = load_sections(book_slug, n, settings)
        if not sections:
            continue
        conn.execute(
            "DELETE FROM sections WHERE book_slug = ? AND chapter = ?",
            (book_slug, n),
        )
        seen_ids: set[str] = set()
        for sec in sections:
            sid = sec.id
            if sid in seen_ids:
                # Harden against rare duplicate ids from section split.
                suffix = 2
                while f"{sec.id}~{suffix}" in seen_ids:
                    suffix += 1
                sid = f"{sec.id}~{suffix}"
            seen_ids.add(sid)
            conn.execute(
                """
                INSERT INTO sections (book_slug, chapter, section_id, title, text, order_idx)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (book_slug, n, sid, sec.title, sec.text, sec.order),
            )
            total += 1
    conn.commit()
    return total


def sync_runs(
    conn: sqlite3.Connection,
    settings: Settings | None = None,
) -> int:
    """Import runs from data/runs/index.jsonl and each run folder's items.jsonl."""
    settings = settings or get_settings()
    runs_dir = Path(settings.data_dir) / "runs"
    index_path = runs_dir / "index.jsonl"
    count = 0
    if not index_path.is_file():
        # Fallback: scan run directories
        if not runs_dir.is_dir():
            return 0
        for child in sorted(runs_dir.iterdir()):
            if child.is_dir() and (child / "request.json").is_file():
                count += _import_run_dir(conn, child)
        conn.commit()
        return count

    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = str(row.get("run_id") or "")
        if not run_id:
            continue
        path = Path(str(row.get("path") or (runs_dir / run_id)))
        conn.execute(
            """
            INSERT INTO runs (run_id, chapter, path, finished_at, question_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                chapter=excluded.chapter,
                path=excluded.path,
                finished_at=excluded.finished_at,
                question_count=excluded.question_count
            """,
            (
                run_id,
                row.get("chapter"),
                str(path),
                row.get("finished_at"),
                row.get("question_count"),
            ),
        )
        if path.is_dir():
            _import_run_items(conn, run_id, path / "items.jsonl")
        count += 1
    conn.commit()
    return count


def _import_run_dir(conn: sqlite3.Connection, path: Path) -> int:
    run_id = path.name
    req: dict[str, Any] = {}
    req_path = path / "request.json"
    if req_path.is_file():
        try:
            req = json.loads(req_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            req = {}
    metrics: dict[str, Any] = {}
    metrics_path = path / "metrics.json"
    if metrics_path.is_file():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            metrics = {}
    conn.execute(
        """
        INSERT INTO runs (run_id, chapter, path, finished_at, question_count)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            chapter=excluded.chapter,
            path=excluded.path,
            finished_at=excluded.finished_at,
            question_count=excluded.question_count
        """,
        (
            run_id,
            req.get("chapter"),
            str(path),
            metrics.get("finished_at") or req.get("started_at"),
            metrics.get("accepted"),
        ),
    )
    _import_run_items(conn, run_id, path / "items.jsonl")
    return 1


def _import_run_items(conn: sqlite3.Connection, run_id: str, items_path: Path) -> None:
    conn.execute("DELETE FROM run_items WHERE run_id = ?", (run_id,))
    if not items_path.is_file():
        return
    for line in items_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        conn.execute(
            """
            INSERT INTO run_items (run_id, status, section_id, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                run_id,
                payload.get("status"),
                payload.get("section_id"),
                json.dumps(payload, ensure_ascii=False),
            ),
        )


def sync_from_filesystem(
    db_path: Path | None = None,
    book_slug: str = "physics10-riazi-110209",
    settings: Settings | None = None,
) -> dict[str, int]:
    """
    Full sync: sections + runs into SQLite.

    Not used by generate — Phase 2 offline store for future tooling.
    """
    settings = settings or get_settings()
    conn = connect(db_path, settings)
    try:
        n_sec = sync_sections(conn, book_slug=book_slug, settings=settings)
        n_runs = sync_runs(conn, settings=settings)
        return {"sections": n_sec, "runs": n_runs, "db": str(db_path or default_db_path(settings))}
    finally:
        conn.close()


def count_sections(
    conn: sqlite3.Connection,
    *,
    book_slug: str | None = None,
    chapter: int | None = None,
) -> int:
    sql = "SELECT COUNT(*) AS c FROM sections WHERE 1=1"
    args: list[Any] = []
    if book_slug is not None:
        sql += " AND book_slug = ?"
        args.append(book_slug)
    if chapter is not None:
        sql += " AND chapter = ?"
        args.append(chapter)
    row = conn.execute(sql, args).fetchone()
    return int(row["c"]) if row else 0
