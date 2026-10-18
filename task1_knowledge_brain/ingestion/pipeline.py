"""Ingestion job: sitemap -> diff -> fetch changed pages -> parse -> store -> index -> describe images.

Every page is committed on its own, so if the job stops halfway the next run
simply continues with the pages that are still pending or failed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

from task1_knowledge_brain.config import Settings
from task1_knowledge_brain.ingestion.crawler import PoliteFetcher
from task1_knowledge_brain.ingestion.parser import ParsedArticle, Section, parse_article
from task1_knowledge_brain.ingestion.sitemap import (
    SitemapEntry, diff_against_manifest, parse_sitemap, record_entries,
)
from task1_knowledge_brain.retrieval.chunker import chunk_article, make_chunk_id
from task1_knowledge_brain.retrieval.index import ChunkIndex

log = logging.getLogger(__name__)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunReport:
    run_id: int = 0
    mode: str = "incremental"
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    failed: int = 0
    images_described: int = 0
    message: str = ""
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- article storage
def _sections_to_json(article: ParsedArticle) -> str:
    return json.dumps([asdict(s) for s in article.sections])


def article_from_row(row: sqlite3.Row, sections_json: str | None = None) -> ParsedArticle:
    sections = [Section(**s) for s in json.loads(sections_json or row["sections_json"])]
    return ParsedArticle(article_id=row["article_id"], url=row["url"], title=row["title"],
                         category=row["category"] or "", folder=row["folder"] or "",
                         sections=sections, images=[])


def store_article(conn: sqlite3.Connection, index: ChunkIndex, article: ParsedArticle,
                  lastmod: str | None, chunk_strategy: str = "section") -> str:
    """Save an article and (re)index it. Returns 'added', 'updated' or 'unchanged'."""
    existing = conn.execute("SELECT content_hash, version, active FROM articles WHERE article_id = ?",
                            (article.article_id,)).fetchone()
    content_hash = article.content_hash
    sections_json = _sections_to_json(article)
    ts = now()

    if existing and existing["content_hash"] == content_hash and existing["active"]:
        with conn:
            conn.execute("UPDATE articles SET lastmod = ? WHERE article_id = ?", (lastmod, article.article_id))
        outcome = "unchanged"
    else:
        version = (existing["version"] + 1) if existing else 1
        with conn:
            conn.execute(
                """INSERT INTO articles(article_id, url, title, category, folder, body_md, sections_json,
                                        lastmod, content_hash, version, active, ingested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(article_id) DO UPDATE SET url=excluded.url, title=excluded.title,
                       category=excluded.category, folder=excluded.folder, body_md=excluded.body_md,
                       sections_json=excluded.sections_json, lastmod=excluded.lastmod,
                       content_hash=excluded.content_hash, version=excluded.version, active=1,
                       ingested_at=excluded.ingested_at""",
                (article.article_id, article.url, article.title, article.category, article.folder,
                 article.body_md, sections_json, lastmod, content_hash, version, ts))
            conn.execute(
                """INSERT OR REPLACE INTO article_versions(article_id, version, content_hash, body_md,
                                                          sections_json, ingested_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (article.article_id, version, content_hash, article.body_md, sections_json, ts))
        index.replace_article_chunks(article.article_id, chunk_article(article, chunk_strategy), "article")
        outcome = "updated" if existing else "added"

    _sync_images(conn, index, article)
    if outcome == "unchanged":
        index.set_article_active(article.article_id, True)
    return outcome


def _sync_images(conn: sqlite3.Connection, index: ChunkIndex, article: ParsedArticle) -> None:
    """Keep the image table in step with the article; drop images that disappeared."""
    new_urls = {img.url for img in article.images}
    old_urls = {r[0] for r in conn.execute("SELECT image_url FROM images WHERE article_id = ?",
                                           (article.article_id,))}
    with conn:
        for url in old_urls - new_urls:
            conn.execute("DELETE FROM images WHERE article_id = ? AND image_url = ?", (article.article_id, url))
            cid = make_chunk_id(article.article_id, "image", url)
            conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (cid,))
            conn.execute("DELETE FROM chunks WHERE chunk_id = ?", (cid,))
        for img in article.images:
            conn.execute(
                """INSERT INTO images(image_url, article_id, position, alt, context, section, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')
                   ON CONFLICT(article_id, image_url) DO UPDATE SET position=excluded.position,
                       alt=excluded.alt, context=excluded.context, section=excluded.section""",
                (img.url, article.article_id, img.position, img.alt, img.context, img.section))
    if old_urls - new_urls:
        index.invalidate()


def deactivate_article(conn: sqlite3.Connection, index: ChunkIndex, url: str, reason: str) -> None:
    row = conn.execute("SELECT article_id FROM pages WHERE url = ?", (url,)).fetchone()
    with conn:
        conn.execute("UPDATE pages SET status = 'removed', last_error = ?, in_sitemap = 0 WHERE url = ?",
                     (reason, url))
        if row and row["article_id"]:
            conn.execute("UPDATE articles SET active = 0 WHERE article_id = ?", (row["article_id"],))
    if row and row["article_id"]:
        index.set_article_active(row["article_id"], False)


def rollback_article(conn: sqlite3.Connection, index: ChunkIndex, article_id: str, version: int) -> None:
    """Restore an earlier stored version of an article (e.g. after a bad parse)."""
    old = conn.execute("SELECT * FROM article_versions WHERE article_id = ? AND version = ?",
                       (article_id, version)).fetchone()
    current = conn.execute("SELECT * FROM articles WHERE article_id = ?", (article_id,)).fetchone()
    if old is None or current is None:
        raise ValueError(f"No version {version} for article {article_id}")
    article = article_from_row(current, old["sections_json"])
    with conn:
        conn.execute("""UPDATE articles SET body_md = ?, sections_json = ?, content_hash = ?, version = ?
                        WHERE article_id = ?""",
                     (old["body_md"], old["sections_json"], old["content_hash"], version, article_id))
    index.replace_article_chunks(article_id, chunk_article(article), "article")


# ---------------------------------------------------------------- the job
async def process_entry(conn: sqlite3.Connection, index: ChunkIndex, fetcher: PoliteFetcher,
                        entry: SitemapEntry, report: RunReport) -> None:
    result = await fetcher.fetch(entry.url)
    attempts_sql = "attempts = attempts + ?"
    if result.status == "gone":
        deactivate_article(conn, index, entry.url, result.error)
        report.removed += 1
        return
    if result.status in ("failed", "blocked"):
        with conn:
            conn.execute(f"""UPDATE pages SET status = ?, {attempts_sql}, last_error = ?, lastmod = ?
                             WHERE url = ?""",
                         ("failed" if result.status == "failed" else "blocked", result.attempts,
                          result.error, entry.lastmod if result.status == "blocked" else None, entry.url))
        report.failed += 1
        report.errors.append(f"{entry.url}: {result.error}")
        return
    try:
        article = parse_article(result.text, entry.url, entry.article_id or "")
        outcome = store_article(conn, index, article, entry.lastmod)
    except Exception as exc:  # ParseError or anything else: one bad page must not stop the job
        log.exception("Failed to process %s", entry.url)
        with conn:
            conn.execute(f"UPDATE pages SET status = 'failed', {attempts_sql}, last_error = ? WHERE url = ?",
                         (result.attempts, f"{type(exc).__name__}: {exc}", entry.url))
        report.failed += 1
        report.errors.append(f"{entry.url}: {exc}")
        return
    with conn:
        conn.execute(f"""UPDATE pages SET status = 'done', {attempts_sql}, last_error = NULL, lastmod = ?,
                                content_hash = ?, fetched_at = ? WHERE url = ?""",
                     (result.attempts, entry.lastmod, article.content_hash, now(), entry.url))
    setattr(report, outcome, getattr(report, outcome) + 1)


async def run_ingestion(
    conn: sqlite3.Connection,
    index: ChunkIndex,
    fetcher: PoliteFetcher,
    settings: Settings,
    *,
    mode: str = "incremental",
    limit: int | None = None,
    describe_images: bool = True,
    image_fetcher: PoliteFetcher | None = None,
    llm=None,
) -> RunReport:
    report = RunReport(mode=mode)
    with conn:
        cur = conn.execute("INSERT INTO ingestion_runs(mode, status, started_at) VALUES (?, 'running', ?)",
                           (mode, now()))
    report.run_id = cur.lastrowid
    try:
        sm = await fetcher.fetch(settings.sitemap_url, check_robots=False)
        if sm.status != "ok":
            raise RuntimeError(f"Could not download sitemap: {sm.error}")
        entries = parse_sitemap(sm.text, allowed_host=urlparse(settings.sitemap_url).netloc)
        diff = diff_against_manifest(conn, entries)  # diff first, so new pages count as "new"
        record_entries(conn, entries)

        for url in diff.removed:
            deactivate_article(conn, index, url, "no longer in sitemap")
            report.removed += 1

        to_fetch = diff.to_fetch() if mode == "incremental" else diff.to_fetch() + diff.unchanged
        report.unchanged = len(diff.unchanged) if mode == "incremental" else 0
        if limit is not None:
            to_fetch = to_fetch[:limit]
        log.info("Sitemap: %d new, %d changed, %d retry, %d unchanged, %d removed; fetching %d",
                 len(diff.new), len(diff.changed), len(diff.retry), len(diff.unchanged),
                 len(diff.removed), len(to_fetch))

        # Process in small batches so progress is visible in /ingestion/status.
        batch = max(1, settings.crawl_concurrency * 2)
        for i in range(0, len(to_fetch), batch):
            await asyncio.gather(*(process_entry(conn, index, fetcher, e, report)
                                   for e in to_fetch[i:i + batch]))
            _save_progress(conn, report, "running")

        if describe_images:
            if llm is None or image_fetcher is None:
                report.message = "Image description skipped (no LLM configured)."
            else:
                from task1_knowledge_brain.multimodal.pipeline import describe_pending_images

                report.images_described = await describe_pending_images(
                    conn, index, llm, image_fetcher, settings, report)
        _save_progress(conn, report, "finished")
    except Exception as exc:
        report.message = f"{type(exc).__name__}: {exc}"
        _save_progress(conn, report, "failed")
        raise
    return report


def _save_progress(conn: sqlite3.Connection, report: RunReport, status: str) -> None:
    with conn:
        conn.execute(
            """UPDATE ingestion_runs SET status = ?, added = ?, updated = ?, removed = ?, unchanged = ?,
                   failed = ?, images_described = ?, message = ?, finished_at = ?
               WHERE id = ?""",
            (status, report.added, report.updated, report.removed, report.unchanged, report.failed,
             report.images_described, report.message, now() if status != "running" else None,
             report.run_id))
