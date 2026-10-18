"""CLI:  python -m task1_knowledge_brain.ingestion [--mode incremental|full] [--limit N] [--no-images]"""

from __future__ import annotations

import argparse
import asyncio
import logging

from task1_knowledge_brain.service import build_service


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl the help center and update the knowledge base.")
    parser.add_argument("--mode", choices=["incremental", "full"], default="incremental")
    parser.add_argument("--limit", type=int, default=None, help="Fetch at most N pages (for testing).")
    parser.add_argument("--no-images", action="store_true", help="Skip screenshot description.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service = build_service()
    report = asyncio.run(service.run_ingestion(mode=args.mode, limit=args.limit,
                                               describe_images=not args.no_images))
    print(f"Run {report.run_id}: added={report.added} updated={report.updated} unchanged={report.unchanged} "
          f"removed={report.removed} failed={report.failed} images={report.images_described} {report.message}")
    for err in report.errors[:20]:
        print("  error:", err)


if __name__ == "__main__":
    main()
