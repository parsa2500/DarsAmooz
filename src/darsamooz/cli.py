from __future__ import annotations

from pathlib import Path

import click

from darsamooz import __version__
from darsamooz.config import get_settings
from darsamooz.export import export_csv, export_json
from darsamooz.generate import generate_from_chapter, load_sample_questions
from darsamooz.ingest import PHYSICS10_CHAPTERS, ingest_pdf


@click.group()
@click.version_option(__version__, prog_name="darsamooz")
def main() -> None:
    """درس‌آموز — تولید تست چهارگزینه‌ای از PDF کتاب درسی."""


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
    """Extract text from a PDF and split into chapters."""
    chapters = ingest_pdf(pdf, book_slug=slug)
    for ch in chapters:
        click.echo(f"فصل {ch.number}: {ch.title} — {len(ch.text)} chars")
    click.echo(f"Saved under {get_settings().extracted_dir}/")


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
    if sample:
        if chapter != 1:
            raise click.ClickException("Bundled sample is only for chapter 1")
        questions = load_sample_questions()[:count]
        click.echo(f"Loaded {len(questions)} SAMPLE questions (not LLM-generated).")
    else:
        if not settings.api_key:
            raise click.ClickException(
                "AGENTROUTER_API_KEY (or OPENAI_API_KEY) missing. Set it in .env, or pass --sample for demo output."
            )
        questions = generate_from_chapter(chapter, count=count, settings=settings)
        click.echo(f"Generated {len(questions)} questions.")

    prefix = out_prefix or f"phys10-ch{chapter}"
    json_path = settings.exports_dir / f"{prefix}.json"
    csv_path = settings.exports_dir / f"{prefix}.csv"
    export_json(questions, json_path)
    export_csv(questions, csv_path)
    click.echo(f"JSON: {json_path}")
    click.echo(f"CSV:  {csv_path}")


if __name__ == "__main__":
    main()
