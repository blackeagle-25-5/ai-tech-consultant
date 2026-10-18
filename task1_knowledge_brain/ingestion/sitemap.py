"""Read the sitemap and work out what changed since the last run."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from urllib.parse import urlparse
from xml.etree import ElementTree

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
ARTICLE_RE = re.compile(r"/support/solutions/articles/(\d+)")
FOLDER_RE = re.compile(r"/support/solutions/folders/(\d+)")


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: str | None
    kind: str  # article | folder | other
    article_id: str | None


@dataclass
class SitemapDiff:
    new: list[SitemapEntry] = field(default_factory=list)
    changed: list[SitemapEntry] = field(default_factory=list)
    unchanged: list[SitemapEntry] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)  # URLs no longer listed
    retry: list[SitemapEntry] = field(default_factory=list)  # previously failed / unfinished

    def to_fetch(self) -> list[SitemapEntry]:
        return self.new + self.changed + self.retry


def classify(url: str) -> tuple[str, str | None]:
    if m := ARTICLE_RE.search(url):
        return "article", m.group(1)
    if FOLDER_RE.search(url):
        return "folder", None
    return "other", None


def parse_sitemap(xml_text: str, allowed_host: str | None = None) -> list[SitemapEntry]:
    """Parse sitemap XML into unique, validated entries (first occurrence wins)."""
    root = ElementTree.fromstring(xml_text)
    seen: set[str] = set()
    entries: list[SitemapEntry] = []
    for node in root.findall("sm:url", NS):
        loc = (node.findtext("sm:loc", default="", namespaces=NS) or "").strip()
        parsed = urlparse(loc)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        if allowed_host and parsed.netloc != allowed_host:
            continue
        if loc in seen:
            continue
        seen.add(loc)
        lastmod = node.findtext("sm:lastmod", default=None, namespaces=NS)
        kind, article_id = classify(loc)
        entries.append(SitemapEntry(url=loc, lastmod=lastmod.strip() if lastmod else None,
                                    kind=kind, article_id=article_id))
    return entries


def diff_against_manifest(conn: sqlite3.Connection, entries: list[SitemapEntry]) -> SitemapDiff:
    """Compare sitemap entries with the saved manifest. Only articles are fetched."""
    known = {row["url"]: row for row in conn.execute("SELECT url, lastmod, status FROM pages")}
    diff = SitemapDiff()
    listed = set()
    for e in entries:
        listed.add(e.url)
        if e.kind != "article":
            continue
        row = known.get(e.url)
        if row is None or row["status"] == "removed":
            diff.new.append(e)
        elif row["status"] in ("pending", "failed"):
            diff.retry.append(e)
        elif (e.lastmod or "") != (row["lastmod"] or ""):
            diff.changed.append(e)
        else:
            diff.unchanged.append(e)
    for url, row in known.items():
        if url not in listed and row["status"] != "removed":
            diff.removed.append(url)
    return diff


def record_entries(conn: sqlite3.Connection, entries: list[SitemapEntry]) -> None:
    """Upsert every sitemap URL into the manifest so a restart knows about it.

    The stored `lastmod` is only updated after a successful fetch (see crawler),
    so a changed page stays "changed" until it is actually reprocessed.
    """
    with conn:
        for e in entries:
            status = "pending" if e.kind == "article" else "skipped"
            conn.execute(
                """INSERT INTO pages(url, kind, article_id, lastmod, status, in_sitemap)
                   VALUES (?, ?, ?, NULL, ?, 1)
                   ON CONFLICT(url) DO UPDATE SET in_sitemap = 1""",
                (e.url, e.kind, e.article_id, status),
            )
