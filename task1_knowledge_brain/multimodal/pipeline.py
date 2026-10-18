"""Describe screenshots that are still pending and add them to the search index."""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from common.llm import LLM, LLMError
from task1_knowledge_brain.config import Settings
from task1_knowledge_brain.ingestion.crawler import PoliteFetcher
from task1_knowledge_brain.multimodal.describe import (
    ImageDescription, SkipImage, describe_image_bytes, description_to_text,
)
from task1_knowledge_brain.retrieval.chunker import image_chunk
from task1_knowledge_brain.retrieval.index import ChunkIndex

log = logging.getLogger(__name__)


async def describe_pending_images(conn: sqlite3.Connection, index: ChunkIndex, llm: LLM,
                                  fetcher: PoliteFetcher, settings: Settings, report) -> int:
    rows = conn.execute(
        """SELECT i.image_url, i.article_id, i.position, i.context, i.section, a.title, a.url
           FROM images i JOIN articles a ON a.article_id = i.article_id
           WHERE i.status = 'pending' AND a.active = 1
           ORDER BY i.article_id, i.position LIMIT ?""",
        (settings.max_images,)).fetchall()
    done = 0
    sem = asyncio.Semaphore(4)

    async def one(row: sqlite3.Row) -> None:
        nonlocal done
        async with sem:
            # Reuse an existing description if the same image appears in another article.
            cached = conn.execute(
                "SELECT description, model FROM images WHERE image_url = ? AND status = 'done' LIMIT 1",
                (row["image_url"],)).fetchone()
            status, description, model = "failed", None, settings.vision_model
            if cached:
                status, description, model = "done", cached["description"], cached["model"]
            else:
                result = await fetcher.fetch(row["image_url"], check_robots=False)
                if result.status != "ok":
                    log.warning("Image fetch failed %s: %s", row["image_url"], result.error)
                else:
                    try:
                        res = await asyncio.to_thread(
                            describe_image_bytes, llm, result.content, result.content_type,
                            row["image_url"], article_title=row["title"], section=row["section"] or "",
                            context=row["context"] or "", model=settings.vision_model)
                        status, description = "done", res.value.model_dump_json()
                        if not res.value.is_informative:
                            status = "skipped"
                    except SkipImage as exc:
                        status, description = "skipped", f'{{"skip_reason": "{exc}"}}'
                    except LLMError as exc:
                        log.warning("Vision failed %s: %s", row["image_url"], exc)
            with conn:
                conn.execute("UPDATE images SET status = ?, description = ?, model = ? "
                             "WHERE article_id = ? AND image_url = ?",
                             (status, description, model, row["article_id"], row["image_url"]))
            if status == "done" and description:
                d = ImageDescription.model_validate_json(description)
                if d.is_informative:
                    index.upsert_chunk(image_chunk(
                        row["article_id"], row["title"], row["url"], row["section"] or "",
                        row["position"], row["image_url"], row["context"] or "", description_to_text(d)))
                    done += 1

    await asyncio.gather(*(one(r) for r in rows))
    return done
