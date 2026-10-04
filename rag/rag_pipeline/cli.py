"""CLI interface for the RAG pipeline using Typer with rich output."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="rag",
    help="Production-grade RAG pipeline — ingest documents & query with hybrid search + reranking.",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(verbose: bool = False) -> None:
    """Configure structured logging with Rich."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


# =========================================================================
# rag ingest
# =========================================================================

@app.command()
def ingest(
    path: str = typer.Argument(..., help="File or directory to ingest"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Ingest documents into the RAG knowledge base.

    Accepts a single file or a directory (recursively ingests all supported files).
    Supported formats: .pdf, .md, .markdown, .mdx
    """
    _setup_logging(verbose)
    from rag_pipeline.pipelines.ingestion import IngestionPipeline

    target = Path(path)
    if not target.exists():
        console.print(f"[red]Error:[/red] Path does not exist: {path}")
        raise typer.Exit(1)

    pipeline = IngestionPipeline()

    with console.status("[bold green]Ingesting...", spinner="dots"):
        if target.is_dir():
            stats = pipeline.ingest_directory(target)
        else:
            stats = pipeline.ingest(target)

    # Display results
    if stats.errors:
        console.print(f"\n[yellow]⚠ {len(stats.errors)} error(s) occurred:[/yellow]")
        for err in stats.errors:
            console.print(f"  [red]•[/red] {err}")

    table = Table(title="Ingestion Complete", show_header=False, border_style="green")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Documents processed", str(stats.documents_processed))
    table.add_row("Chunks created", str(stats.chunks_created))
    table.add_row("Embeddings generated", str(stats.embeddings_generated))
    table.add_row("Points upserted", str(stats.points_upserted))
    table.add_row("Time elapsed", f"{stats.elapsed_seconds:.2f}s")
    console.print(table)


# =========================================================================
# rag query
# =========================================================================

@app.command()
def query(
    question: str = typer.Argument(..., help="Your question"),
    top_k: int = typer.Option(20, "--top-k", "-k", help="Retrieval candidates"),
    top_n: int = typer.Option(5, "--top-n", "-n", help="Final results after reranking"),
    show_sources: bool = typer.Option(True, "--sources/--no-sources", help="Show source attribution"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Query the knowledge base and get assembled context.

    The output is a fully assembled prompt ready for any LLM.
    """
    _setup_logging(verbose)
    from rag_pipeline.pipelines.query import QueryPipeline

    pipeline = QueryPipeline()

    with console.status("[bold green]Searching...", spinner="dots"):
        result = pipeline.query(question=question, top_k=top_k, top_n=top_n)

    # Display assembled context
    ctx = result.assembled_context

    if ctx.total_chunks_used == 0:
        console.print("[yellow]No relevant context found in the knowledge base.[/yellow]")
        raise typer.Exit(0)

    console.print(Panel(
        ctx.prompt,
        title="[bold green]Assembled Prompt[/bold green]",
        border_style="green",
        padding=(1, 2),
    ))

    # Display sources
    if show_sources and ctx.sources:
        source_table = Table(title="Sources", border_style="cyan")
        source_table.add_column("#", justify="right", width=3)
        source_table.add_column("File")
        source_table.add_column("Page", justify="center")
        source_table.add_column("Chunk", justify="center")
        source_table.add_column("Heading Path")
        source_table.add_column("Score", justify="right")

        for i, src in enumerate(ctx.sources, 1):
            source_table.add_row(
                str(i),
                src.get("file_name", "—"),
                str(src.get("page_number", "—")),
                str(src.get("chunk_index", "—")),
                src.get("heading_path", "—") or "—",
                f"{src.get('rerank_score', 0):.4f}",
            )

        console.print(source_table)

    # Timing info
    console.print(
        f"\n[dim]Retrieved {len(result.retrieval_results)} candidates → "
        f"Reranked to {len(result.reranked_results)} → "
        f"Used {ctx.total_chunks_used} in context "
        f"({ctx.total_chars} chars) — {result.elapsed_seconds:.2f}s[/dim]"
    )


# =========================================================================
# rag status
# =========================================================================

@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show collection statistics and health."""
    _setup_logging(verbose)
    from rag_pipeline.vectorstore.qdrant_store import QdrantStore

    store = QdrantStore()
    info = store.get_collection_info()

    if "error" in info:
        console.print(f"[red]Error:[/red] {info['error']}")
        console.print("[dim]Is Qdrant running? Try: docker run -p 6333:6333 qdrant/qdrant[/dim]")
        raise typer.Exit(1)

    table = Table(title="Collection Status", show_header=False, border_style="blue")
    table.add_column("Property", style="bold")
    table.add_column("Value", justify="right")

    for key, value in info.items():
        table.add_row(key.replace("_", " ").title(), str(value))

    console.print(table)


# =========================================================================
# rag delete
# =========================================================================

@app.command()
def delete(
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Delete the entire collection (destructive!)."""
    _setup_logging(verbose)
    from rag_pipeline.vectorstore.qdrant_store import QdrantStore

    if not confirm:
        confirm = typer.confirm("⚠ Delete the entire collection? This cannot be undone")
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(0)

    store = QdrantStore()
    success = store.delete_collection()

    if success:
        console.print("[green]✓[/green] Collection deleted successfully.")
    else:
        console.print("[red]✗[/red] Failed to delete collection.")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
