"""Turn a help-center article page into clean, structured text.

Keeps headings, lists, tables and the position of every screenshot, plus the
paragraph just before each screenshot (the text that explains it).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

BLOCK_TAGS = {"p", "div", "h2", "h3", "h4", "h5", "ul", "ol", "table", "pre", "blockquote", "section"}
HEADING_TAGS = {"h2": 2, "h3": 3, "h4": 4, "h5": 5}
SKIP_IMAGE_PATTERNS = ("/hit", "logo", "data:", "marketplacebanner", "emoji", "gravatar")


@dataclass
class ImageRef:
    url: str
    position: int
    alt: str
    context: str
    section: str


@dataclass
class Section:
    heading: str
    level: int
    lines: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line for line in self.lines if line.strip())


@dataclass
class ParsedArticle:
    article_id: str
    url: str
    title: str
    category: str
    folder: str
    sections: list[Section]
    images: list[ImageRef]

    @property
    def body_md(self) -> str:
        parts = []
        for s in self.sections:
            if s.heading:
                parts.append("#" * s.level + " " + s.heading)
            parts.append(s.text)
        return "\n\n".join(p for p in parts if p.strip())

    @property
    def content_hash(self) -> str:
        payload = self.title + "\n" + self.body_md + "\n" + "\n".join(i.url for i in self.images)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ParseError(ValueError):
    pass


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _has_block_children(tag: Tag) -> bool:
    return any(isinstance(c, Tag) and c.name in BLOCK_TAGS for c in tag.children)


class _Walker:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.sections: list[Section] = [Section(heading="", level=2)]
        self.images: list[ImageRef] = []
        self.last_text = ""

    @property
    def current(self) -> Section:
        return self.sections[-1]

    def add_line(self, line: str) -> None:
        line = line.strip()
        if line:
            self.current.lines.append(line)
            self.last_text = line

    def collect_images(self, tag: Tag) -> list[str]:
        markers = []
        for img in tag.find_all("img"):
            src = (img.get("src") or img.get("data-src") or "").strip()
            if not src or any(p in src.lower() for p in SKIP_IMAGE_PATTERNS):
                continue
            url = urljoin(self.base_url, src)
            if not url.startswith(("http://", "https://")):
                continue
            n = len(self.images) + 1
            self.images.append(ImageRef(
                url=url, position=n, alt=_clean(img.get("alt") or ""),
                context=self.last_text[:500], section=self.current.heading,
            ))
            self.current.image_urls.append(url)
            markers.append(f"[Screenshot {n}]")
        return markers

    def walk(self, node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                self.add_line(_clean(str(child)))
                continue
            if not isinstance(child, Tag) or child.name in ("script", "style", "noscript"):
                continue
            name = child.name
            if name in HEADING_TAGS:
                heading = _clean(child.get_text(" "))
                if heading:
                    self.sections.append(Section(heading=heading, level=HEADING_TAGS[name]))
                    self.last_text = heading
                continue
            if name in ("ul", "ol"):
                for i, li in enumerate(child.find_all("li", recursive=False), start=1):
                    bullet = f"{i}." if name == "ol" else "-"
                    text = _clean(li.get_text(" "))
                    markers = self.collect_images(li)
                    self.add_line(" ".join([f"{bullet} {text}"] + markers))
                continue
            if name == "table":
                self.add_table(child)
                continue
            if name in ("div", "section", "blockquote") and _has_block_children(child):
                self.walk(child)
                continue
            text = _clean(child.get_text(" "))
            if text:
                self.add_line(text)
            markers = self.collect_images(child) if name != "img" else self.collect_images_from_img(child)
            if markers:
                self.add_line(" ".join(markers))

    def collect_images_from_img(self, img: Tag) -> list[str]:
        wrapper = BeautifulSoup("<span></span>", "lxml").span
        wrapper.append(img.__copy__())
        return self.collect_images(wrapper)

    def add_table(self, table: Tag) -> None:
        rows = []
        for tr in table.find_all("tr"):
            cells = [_clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append("| " + " | ".join(cells) + " |")
        if rows:
            header_cols = rows[0].count("|") - 1
            rows.insert(1, "|" + " --- |" * header_cols)
            # Keep the whole table as one line block so the chunker never splits header from rows.
            self.add_line("\n".join(rows))
        self.collect_images(table)


def parse_article(html: str, url: str, article_id: str) -> ParsedArticle:
    soup = BeautifulSoup(html, "lxml")
    body = soup.select_one("article.article-body") or soup.select_one("#article-body")
    if body is None:
        raise ParseError(f"No article body found at {url}")

    title_tag = body.select_one("h1")
    title = _clean(title_tag.get_text(" ")) if title_tag else ""
    if not title:
        og = soup.find("meta", property="og:title")
        title = _clean(og["content"]) if og and og.get("content") else url
    for junk in body.select("h1, .the-article-link, script, style, .copy-link-to-section"):
        junk.decompose()

    crumbs = [_clean(a.get_text(" ")) for a in soup.select(".breadcrumb a")]
    category = crumbs[1].removeprefix("Section:").strip() if len(crumbs) > 1 else ""
    folder = crumbs[2] if len(crumbs) > 2 else ""

    walker = _Walker(base_url=url)
    walker.walk(body)
    sections = [s for s in walker.sections if s.text or s.image_urls]
    if not sections:
        sections = [Section(heading="", level=2, lines=[title])]
    return ParsedArticle(
        article_id=article_id, url=url, title=title, category=category, folder=folder,
        sections=sections, images=walker.images,
    )
