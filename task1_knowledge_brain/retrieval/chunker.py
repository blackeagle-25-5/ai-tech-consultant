"""Split parsed articles into searchable chunks.

Two strategies so the evals can compare them:
- "section": split at headings; merge tiny sections; split big ones at line
  boundaries with a one-line overlap. Tables are single lines, so they never split.
- "fixed": fixed-size character windows with overlap, ignoring structure.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from task1_knowledge_brain.ingestion.parser import ParsedArticle

MAX_CHARS = 1200
MIN_CHARS = 300


@dataclass
class Chunk:
    chunk_id: str
    article_id: str
    source_type: str  # article | image | expert
    title: str
    url: str
    section: str
    text: str
    image_urls: list[str] = field(default_factory=list)

    @property
    def image_urls_json(self) -> str:
        return json.dumps(self.image_urls)


def make_chunk_id(article_id: str, source_type: str, index: int | str) -> str:
    return hashlib.sha1(f"{article_id}:{source_type}:{index}".encode()).hexdigest()[:16]


def _split_lines(lines: list[str], max_chars: int) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) > max_chars:
            groups.append(current)
            current = [current[-1]]  # one-line overlap keeps a step with its lead-in
            size = len(current[0])
        current.append(line)
        size += len(line)
    if current:
        groups.append(current)
    return groups


def chunk_article(article: ParsedArticle, strategy: str = "section") -> list[Chunk]:
    if strategy == "fixed":
        return _chunk_fixed(article)

    # Merge consecutive small sections so each chunk has enough context.
    merged: list[tuple[str, list[str], list[str]]] = []  # (heading, lines, images)
    for s in article.sections:
        lines = ([f"{'#' * s.level} {s.heading}"] if s.heading else []) + [l for l in s.lines if l.strip()]
        if merged and sum(len(l) for l in merged[-1][1]) < MIN_CHARS:
            heading, prev_lines, prev_imgs = merged[-1]
            merged[-1] = (heading or s.heading, prev_lines + lines, prev_imgs + s.image_urls)
        else:
            merged.append((s.heading, lines, list(s.image_urls)))

    chunks: list[Chunk] = []
    for heading, lines, images in merged:
        for group in _split_lines(lines, MAX_CHARS):
            text = f"{article.title}\n" + "\n".join(group)
            idx = len(chunks)
            chunks.append(Chunk(
                chunk_id=make_chunk_id(article.article_id, "article", idx),
                article_id=article.article_id, source_type="article", title=article.title,
                url=article.url, section=heading, text=text, image_urls=images,
            ))
    return chunks


def _chunk_fixed(article: ParsedArticle, size: int = 800, overlap: int = 150) -> list[Chunk]:
    body = article.body_md
    chunks: list[Chunk] = []
    start = 0
    while start < max(len(body), 1):
        piece = body[start:start + size]
        idx = len(chunks)
        chunks.append(Chunk(
            chunk_id=make_chunk_id(article.article_id, "article", idx),
            article_id=article.article_id, source_type="article", title=article.title,
            url=article.url, section="", text=f"{article.title}\n{piece}",
        ))
        start += size - overlap
    return chunks


def image_chunk(article_id: str, title: str, url: str, section: str, position: int,
                image_url: str, context: str, description_text: str) -> Chunk:
    text = (
        f"{title}\nScreenshot {position} (section: {section or 'intro'}). "
        f"Shown after: \"{context[:300]}\". {description_text}"
    )
    return Chunk(
        chunk_id=make_chunk_id(article_id, "image", image_url),
        article_id=article_id, source_type="image", title=title, url=url,
        section=section, text=text, image_urls=[image_url],
    )
