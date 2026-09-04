"""CLI entrypoint: `python -m nexus_vision path/to/file.pdf`

Prints the document JSON to stdout; logs go to stderr, so
`python -m nexus_vision file.pdf > out.json` gives you a clean JSON file.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .pdf_io import PdfOpenError
from .pipeline import process_pdf


def main() -> int:
    parser = argparse.ArgumentParser(description="Local PDF vision pipeline (text + OCR + tables).")
    parser.add_argument("pdf_path", help="Path to the PDF file to process")
    parser.add_argument(
        "--device", default="cpu", help="PaddleOCR device string, e.g. 'cpu', 'gpu', 'gpu:0' (default: cpu)"
    )
    parser.add_argument(
        "--dpi", type=int, default=150,
        help="Render DPI for scanned pages (default: 150 - see pdf_io.render_page_to_image docstring)",
    )
    parser.add_argument(
        "--no-refine-tables", dest="refine_tables", action="store_false",
        help="Skip the higher-DPI table re-OCR pass (see table_refine.py). On by default.",
    )
    parser.add_argument(
        "--table-refine-dpi", type=int, default=300,
        help="DPI for the table-region re-OCR pass, capped automatically for large tables (default: 300)",
    )
    parser.add_argument(
        "--debug-dir", default=None,
        help="Directory to write per-page table/cell bounding-box overlay PNGs to (see debug_viz.py). Off by default.",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON indent for stdout output (default: 2)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)

    try:
        document = process_pdf(
            args.pdf_path,
            device=args.device,
            dpi=args.dpi,
            refine_tables=args.refine_tables,
            table_refine_dpi=args.table_refine_dpi,
            debug_dir=args.debug_dir,
        )
    except PdfOpenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    json.dump(document, sys.stdout, indent=args.indent)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
