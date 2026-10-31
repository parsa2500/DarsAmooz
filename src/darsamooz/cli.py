from __future__ import annotations

import sys
from pathlib import Path

import click

from darsamooz import __version__
from darsamooz.config import get_settings
from darsamooz.export import (
    export_markdown,
    load_questions_from_json,
)
from darsamooz.generate import (
    generate_from_chapter_with_run,
    load_sample_questions,
    persist_generation,
    persist_sample_run,
)
from darsamooz.ingest import PHYSICS10_CHAPTERS, ingest_pdf


def _configure_stdio() -> None:
    """Avoid UnicodeEncodeError on Windows cp1252 consoles when printing Persian."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


@click.group()
@click.version_option(__version__, prog_name="darsamooz")
def main() -> None:
    """درس‌آموز — تولید تست چهارگزینه‌ای از PDF کتاب درسی."""
    _configure_stdio()


@main.command("chapters")
def chapters_cmd() -> None:
    """List known chapters for Physics 10 (math track)."""
    for num, title in PHYSICS10_CHAPTERS:
        click.echo(f"{num}\t{title}")


@main.command("ingest")
@click.option(
    "--pdf",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="Path to textbook PDF",
)
@click.option("--slug", default="physics10-riazi-110209", show_default=True)
def ingest_cmd(pdf: Path, slug: str) -> None:
    """Extract text from a PDF and split into chapters + section maps."""
    chapters = ingest_pdf(pdf, book_slug=slug)
    for ch in chapters:
        click.echo(f"فصل {ch.number}: {ch.title} — {len(ch.text)} chars")
    click.echo(f"Saved under {get_settings().extracted_dir}/")
    click.echo(
        f"Section maps: {get_settings().extracted_dir}/{slug}/chN/sections.json"
    )


@main.command("generate")
@click.option("--chapter", type=int, required=True, help="Chapter number (1-5)")
@click.option("--count", type=int, default=10, show_default=True)
@click.option("--out-prefix", default=None, help="Output file prefix under data/exports/")
@click.option(
    "--sample",
    is_flag=True,
    help="Ignore LLM; emit bundled hand-written sample (chapter 1 only)",
)
def generate_cmd(chapter: int, count: int, out_prefix: str | None, sample: bool) -> None:
    """Generate multiple-choice questions for a chapter."""
    settings = get_settings()
    prefix = out_prefix or f"phys10-ch{chapter}"

    if sample:
        if chapter != 1:
            raise click.ClickException("Bundled sample is only for chapter 1")
        questions = load_sample_questions()[:count]
        click.echo(f"Loaded {len(questions)} SAMPLE questions (not LLM-generated).")
        paths, run = persist_sample_run(
            questions, chapter=chapter, prefix=prefix, settings=settings
        )
        click.echo(f"run_id: {run.run_id}")
        click.echo(f"run: {run.root}")
    else:
        if not settings.api_key:
            raise click.ClickException(
                "API key missing for LLM_PROVIDER="
                f"{settings.provider_name}. Set GEMINI_API_KEY (or OPENAI_API_KEY) "
                "in .env, or pass --sample for demo output."
            )
        try:
            click.echo(
                f"Provider={settings.provider_name} model={settings.model}"
            )
            result = generate_from_chapter_with_run(
                chapter, count=count, settings=settings
            )
        except (RuntimeError, FileNotFoundError) as exc:
            raise click.ClickException(str(exc)) from exc
        questions = result.questions
        click.echo(f"Generated {len(questions)} questions.")
        click.echo(f"run_id: {result.run.run_id}")
        click.echo(f"run: {result.run.root}")
        metrics = result.metrics
        click.echo(
            "metrics: "
            f"rounds={metrics.get('rounds')} accepted={metrics.get('accepted')} "
            f"rejected={metrics.get('rejected')} "
            f"prompt_tokens={metrics.get('prompt_tokens')} "
            f"completion_tokens={metrics.get('completion_tokens')}"
        )
        paths = persist_generation(result, prefix=prefix, settings=settings)

    click.echo(f"JSON: {paths['json']}")
    click.echo(f"CSV:  {paths['csv']}")
    click.echo(f"MD:   {paths['md']}")


@main.command("to-md")
@click.argument("json_path", type=click.Path(path_type=Path, exists=True))
@click.option(
    "--out",
    "out_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Output .md path (default: same stem as JSON)",
)
@click.option("--title", default=None, help="Optional Markdown H1 title override")
def to_md_cmd(json_path: Path, out_path: Path | None, title: str | None) -> None:
    """Convert an existing questions JSON export to RTL Persian Markdown."""
    try:
        questions = load_questions_from_json(json_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    dest = out_path or json_path.with_suffix(".md")
    export_markdown(questions, dest, title=title)
    click.echo(f"MD: {dest} ({len(questions)} questions)")


@main.command("index-build")
@click.option("--chapter", type=int, default=None, help="Chapter number (omit for all)")
@click.option("--slug", default="physics10-riazi-110209", show_default=True)
@click.option("--all", "build_all", is_flag=True, help="Build indexes for all known chapters")
def index_build_cmd(chapter: int | None, slug: str, build_all: bool) -> None:
    """Phase 2 (offline): build BM25 + TF-IDF indexes. Not used by generate."""
    from darsamooz.retrieval import build_all_chapter_indexes, build_chapter_index

    try:
        if build_all or chapter is None:
            results = build_all_chapter_indexes(book_slug=slug)
            if not results:
                raise click.ClickException("No chapter sections found. Run ingest first.")
            for n, info in sorted(results.items()):
                click.echo(
                    f"ch{n}: sections={info['section_count']} "
                    f"bm25={info['bm25']} tfidf={info['tfidf']}"
                )
        else:
            info = build_chapter_index(slug, chapter)
            click.echo(
                f"ch{chapter}: sections={info['section_count']} "
                f"bm25={info['bm25']} tfidf={info['tfidf']}"
            )
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("retrieve")
@click.option("--chapter", type=int, required=True)
@click.option("--query", required=True, help="Persian search query")
@click.option("--k", type=int, default=5, show_default=True)
@click.option(
    "--method",
    type=click.Choice(["bm25", "tfidf"], case_sensitive=False),
    default="bm25",
    show_default=True,
)
@click.option("--slug", default="physics10-riazi-110209", show_default=True)
def retrieve_cmd(chapter: int, query: str, k: int, method: str, slug: str) -> None:
    """Phase 2 (offline): rank sections for a query. Not used by generate."""
    from darsamooz.retrieval import retrieve_sections

    try:
        hits = retrieve_sections(
            slug, chapter, query, k=k, method=method.lower()  # type: ignore[arg-type]
        )
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    if not hits:
        click.echo("No hits.")
        return
    for i, (sec, score) in enumerate(hits, start=1):
        click.echo(f"{i}\t{score:.4f}\t{sec.id}\t{sec.title}")


@main.command("db-sync")
@click.option("--slug", default="physics10-riazi-110209", show_default=True)
@click.option(
    "--db",
    type=click.Path(path_type=Path),
    default=None,
    help="SQLite path (default: data/darsamooz.sqlite)",
)
def db_sync_cmd(slug: str, db: Path | None) -> None:
    """Phase 2 (offline): sync sections + runs into SQLite. Not used by generate."""
    from darsamooz.store import sync_from_filesystem

    stats = sync_from_filesystem(db_path=db, book_slug=slug)
    click.echo(
        f"Synced sections={stats['sections']} runs={stats['runs']} db={stats['db']}"
    )


if __name__ == "__main__":
    main()
