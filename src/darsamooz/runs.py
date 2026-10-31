from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from darsamooz.config import Settings, get_settings
from darsamooz.export import export_csv, export_json, export_markdown
from darsamooz.models import Question


def new_run_id(chapter: int) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{ts}-ch{chapter}"


@dataclass
class RunWriter:
    run_id: str
    chapter: int
    root: Path
    request: dict[str, Any] = field(default_factory=dict)
    units_used: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "export").mkdir(parents=True, exist_ok=True)
        self._items_path = self.root / "items.jsonl"
        if self._items_path.exists():
            self._items_path.unlink()

    @classmethod
    def create(
        cls,
        chapter: int,
        *,
        settings: Settings | None = None,
        request: dict[str, Any] | None = None,
    ) -> RunWriter:
        settings = settings or get_settings()
        run_id = new_run_id(chapter)
        runs_dir = Path(settings.data_dir) / "runs"
        root = runs_dir / run_id
        writer = cls(run_id=run_id, chapter=chapter, root=root, request=request or {})
        writer.write_request()
        return writer

    def write_request(self) -> None:
        path = self.root / "request.json"
        payload = {
            "run_id": self.run_id,
            "chapter": self.chapter,
            **self.request,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def record_unit(self, section_id: str, title: str, chars: int) -> None:
        self.units_used.append(
            {"section_id": section_id, "title": title, "chars": chars}
        )

    def append_item(self, record: dict[str, Any]) -> None:
        with self._items_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_units(self) -> None:
        path = self.root / "units_used.json"
        # Unique by section_id preserving order
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for u in self.units_used:
            sid = u.get("section_id")
            if sid in seen:
                continue
            seen.add(str(sid))
            unique.append(u)
        path.write_text(
            json.dumps({"count": len(unique), "units": unique}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_metrics(self, metrics: dict[str, Any]) -> None:
        self.metrics = metrics
        path = self.root / "metrics.json"
        path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    def finalize_exports(
        self,
        questions: list[Question],
        *,
        prefix: str,
        settings: Settings | None = None,
    ) -> dict[str, Path]:
        settings = settings or get_settings()
        exports = Path(settings.exports_dir)
        exports.mkdir(parents=True, exist_ok=True)
        run_export = self.root / "export"

        paths = {
            "json": exports / f"{prefix}.json",
            "csv": exports / f"{prefix}.csv",
            "md": exports / f"{prefix}.md",
        }
        export_json(questions, paths["json"])
        export_csv(questions, paths["csv"])
        export_markdown(questions, paths["md"])

        for key, src in paths.items():
            dest = run_export / src.name
            shutil.copy2(src, dest)

        self.write_units()
        index_path = Path(settings.data_dir) / "runs" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_row = {
            "run_id": self.run_id,
            "chapter": self.chapter,
            "prefix": prefix,
            "question_count": len(questions),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "path": str(self.root),
        }
        with index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(index_row, ensure_ascii=False) + "\n")

        return paths
