from __future__ import annotations

import json
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
from darsamooz.models import ReviewStatus
from darsamooz.profiles import (
    DEFAULT_PROFILE_ID,
    export_with_profile,
    list_profiles,
)
from darsamooz.review import (
    apply_review_edit,
    filter_by_review_status,
    load_questions_payload,
    reexport_questions,
    review_summary,
    save_questions_payload,
)


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
@click.option(
    "--verify/--no-verify",
    default=True,
    show_default=True,
    help="Blind second-pass answer verification (LLM)",
)
@click.option(
    "--difficulty-mix",
    default=None,
    help="Target mix, e.g. easy=30,medium=50,hard=20",
)
@click.option(
    "--balance-answers/--no-balance-answers",
    default=True,
    show_default=True,
    help="Deterministically redistribute correct options over A–D",
)
@click.option(
    "--approved-only",
    is_flag=True,
    help="Export only approved questions (usually empty right after generate)",
)
@click.option(
    "--export-profile",
    default=None,
    help=f"Also write a mapped client export using profile id (default profiles: {DEFAULT_PROFILE_ID})",
)
def generate_cmd(
    chapter: int,
    count: int,
    out_prefix: str | None,
    sample: bool,
    verify: bool,
    difficulty_mix: str | None,
    balance_answers: bool,
    approved_only: bool,
    export_profile: str | None,
) -> None:
    """Generate multiple-choice questions for a chapter."""
    settings = get_settings()
    prefix = out_prefix or f"phys10-ch{chapter}"

    if sample:
        if chapter != 1:
            raise click.ClickException("Bundled sample is only for chapter 1")
        questions = load_sample_questions()[:count]
        click.echo(f"Loaded {len(questions)} SAMPLE questions (not LLM-generated).")
        paths, run, questions = persist_sample_run(
            questions,
            chapter=chapter,
            prefix=prefix,
            settings=settings,
            balance_answers=balance_answers,
        )
        click.echo(f"run_id: {run.run_id}")
        click.echo(f"run: {run.root}")
        if approved_only:
            paths = run.finalize_exports(
                questions, prefix=prefix, settings=settings, approved_only=True
            )
    else:
        if not settings.api_key:
            raise click.ClickException(
                "API key missing for LLM_PROVIDER="
                f"{settings.provider_name}. Set GEMINI_API_KEY (or OPENAI_API_KEY) "
                "in .env, or pass --sample for demo output."
            )
        try:
            click.echo(
                f"Provider={settings.provider_name} model={settings.model} "
                f"verify={verify}"
            )
            result = generate_from_chapter_with_run(
                chapter,
                count=count,
                settings=settings,
                verify=verify,
                difficulty_mix=difficulty_mix,
                balance_answers=balance_answers,
            )
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
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
            f"completion_tokens={metrics.get('completion_tokens')} "
            f"answers={metrics.get('answer_positions')} "
            f"difficulties={metrics.get('difficulties')} "
            f"needs_review={metrics.get('needs_review')}"
        )
        paths = persist_generation(
            result, prefix=prefix, settings=settings, approved_only=approved_only
        )

    if export_profile:
        mapped = settings.exports_dir / f"{prefix}.{export_profile}.json"
        if not mapped.is_absolute():
            mapped = Path(mapped)
        export_with_profile(
            questions,
            mapped,
            profile_id=export_profile,
            settings=settings,
            approved_only=approved_only,
        )
        click.echo(f"Profile({export_profile}): {mapped}")

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
@click.option("--approved-only", is_flag=True, help="Only include approved questions")
def to_md_cmd(
    json_path: Path, out_path: Path | None, title: str | None, approved_only: bool
) -> None:
    """Convert an existing questions JSON export to RTL Persian Markdown."""
    try:
        questions = load_questions_from_json(json_path)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    if approved_only:
        questions = filter_by_review_status(questions, approved_only=True)
    dest = out_path or json_path.with_suffix(".md")
    if not questions:
        raise click.ClickException("no questions to export (after filters)")
    export_markdown(questions, dest, title=title)
    click.echo(f"MD: {dest} ({len(questions)} questions)")


@main.command("export-client")
@click.argument("json_path", type=click.Path(path_type=Path, exists=True))
@click.option(
    "--profile",
    default=DEFAULT_PROFILE_ID,
    show_default=True,
    help="Profile id under data/export_profiles/",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Output path (.json or .csv per profile format)",
)
@click.option("--approved-only", is_flag=True)
def export_client_cmd(
    json_path: Path, profile: str, out_path: Path, approved_only: bool
) -> None:
    """Export questions using a mapping profile (client import format placeholder)."""
    questions = load_questions_from_json(json_path)
    try:
        path = export_with_profile(
            questions,
            out_path,
            profile_id=profile,
            approved_only=approved_only,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Wrote {path} (profile={profile}, n={len(questions)})")


@main.command("profiles")
def profiles_cmd() -> None:
    """List available client export profiles."""
    items = list_profiles()
    if not items:
        click.echo("No profiles found under data/export_profiles/")
        return
    for p in items:
        click.echo(f"{p['id']}\t{p['format']}\t{p['name']}")


@main.command("review")
@click.argument("json_path", type=click.Path(path_type=Path, exists=True))
@click.option("--question-id", required=True)
@click.option(
    "--status",
    type=click.Choice([s.value for s in ReviewStatus], case_sensitive=False),
    default=None,
)
@click.option("--stem", default=None)
@click.option("--explanation", default=None)
@click.option("--difficulty", type=click.Choice(["easy", "medium", "hard"]), default=None)
@click.option("--correct-index", type=int, default=None)
@click.option(
    "--option",
    "options",
    multiple=True,
    help="Pass four times to replace all options",
)
@click.option("--reexport/--no-reexport", default=True, show_default=True)
def review_cmd(
    json_path: Path,
    question_id: str,
    status: str | None,
    stem: str | None,
    explanation: str | None,
    difficulty: str | None,
    correct_index: int | None,
    options: tuple[str, ...],
    reexport: bool,
) -> None:
    """Approve/reject/edit one question in a JSON export and save in place."""
    meta, questions = load_questions_payload(json_path)
    found = None
    for i, q in enumerate(questions):
        if q.id == question_id:
            found = i
            break
    if found is None:
        raise click.ClickException(f"question id not found: {question_id}")
    patch: dict = {}
    if status:
        patch["review_status"] = status
    if stem is not None:
        patch["stem"] = stem
    if explanation is not None:
        patch["explanation"] = explanation
    if difficulty is not None:
        patch["difficulty"] = difficulty
    if correct_index is not None:
        patch["correct_index"] = correct_index
    if options:
        if len(options) != 4:
            raise click.ClickException("pass exactly four --option values")
        patch["options"] = list(options)
    if not patch:
        raise click.ClickException("nothing to change")
    try:
        questions[found] = apply_review_edit(questions[found], patch)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    save_questions_payload(json_path, questions, meta=meta)
    click.echo(
        f"Updated {question_id} → {questions[found].review_status.value} "
        f"({review_summary(questions)})"
    )
    if reexport:
        csv_path = json_path.with_suffix(".csv")
        md_path = json_path.with_suffix(".md")
        reexport_questions(
            questions, json_path=json_path, csv_path=csv_path, md_path=md_path
        )
        click.echo(f"Re-exported {csv_path.name} and {md_path.name}")


@main.command("export-approved")
@click.argument("json_path", type=click.Path(path_type=Path, exists=True))
@click.option(
    "--out-prefix",
    type=click.Path(path_type=Path),
    required=True,
    help="Output prefix (writes .json/.csv/.md)",
)
def export_approved_cmd(json_path: Path, out_prefix: Path) -> None:
    """Export only approved questions from an existing JSON file."""
    questions = load_questions_from_json(json_path)
    approved = filter_by_review_status(questions, approved_only=True)
    if not approved:
        raise click.ClickException("no approved questions in file")
    paths = reexport_questions(
        approved,
        json_path=Path(str(out_prefix) + ".json"),
        csv_path=Path(str(out_prefix) + ".csv"),
        md_path=Path(str(out_prefix) + ".md"),
    )
    for k, p in paths.items():
        click.echo(f"{k}: {p}")


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
