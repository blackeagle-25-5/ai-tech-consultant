from task1_knowledge_brain.db import connect
from task1_knowledge_brain.ingestion.parser import parse_article
from task1_knowledge_brain.ingestion.sitemap import diff_against_manifest, parse_sitemap, record_entries
from task1_knowledge_brain.retrieval.chunker import chunk_article

from .conftest import FIXTURES, article_html, sitemap_xml

BASE = "https://help.example.com/support/solutions"


def test_parse_real_article_keeps_structure_and_images():
    html = (FIXTURES / "article_with_images.html").read_text(encoding="utf-8")
    a = parse_article(html, f"{BASE}/articles/12000028489-x", "12000028489")
    assert a.title.startswith("How To Change The Name")
    assert a.category == "My Content" and a.folder == "Edit Post Settings"
    assert len(a.images) == 7
    assert "URL Permalink" in [s.heading for s in a.sections]
    # Each screenshot keeps the sentence that introduces it.
    assert "Edit" in a.images[0].context
    assert "[Screenshot 1]" in a.body_md
    # Tracking pixels and the marketplace banner are not treated as screenshots.
    assert not any("/hit" in i.url or "marketplace" in i.url for i in a.images)


def test_parse_lists_and_tables():
    body = ("<p>Intro</p><h2>Steps</h2><ol><li>Open Settings</li><li>Click Save</li></ol>"
            "<table><tr><th>Plan</th><th>Price</th></tr><tr><td>Basic</td><td>$10</td></tr></table>")
    a = parse_article(article_html("Plans", body), f"{BASE}/articles/1-plans", "1")
    text = a.body_md
    assert "1. Open Settings" in text and "2. Click Save" in text
    assert "| Plan | Price |" in text and "| Basic | $10 |" in text


def test_content_hash_changes_with_content():
    a1 = parse_article(article_html("T", "<p>one</p>"), f"{BASE}/articles/1", "1")
    a2 = parse_article(article_html("T", "<p>two</p>"), f"{BASE}/articles/1", "1")
    assert a1.content_hash != a2.content_hash


def test_sitemap_parse_dedupes_and_validates():
    xml = sitemap_xml([
        (f"{BASE}/articles/1-a", "2026-01-01"),
        (f"{BASE}/articles/1-a", "2026-01-01"),
        ("ftp://bad/url", "2026-01-01"),
        (f"{BASE}/folders/9", "2026-01-01"),
        ("https://other.com/support/solutions/articles/2-b", "2026-01-01"),
    ])
    entries = parse_sitemap(xml, allowed_host="help.example.com")
    assert [(e.kind, e.article_id) for e in entries] == [("article", "1"), ("folder", None)]


def test_sitemap_diff_new_changed_unchanged_removed(tmp_path):
    conn = connect(tmp_path / "kb.db")
    first = parse_sitemap(sitemap_xml([(f"{BASE}/articles/1-a", "d1"), (f"{BASE}/articles/2-b", "d1"),
                                       (f"{BASE}/articles/3-c", "d1")]))
    record_entries(conn, first)
    conn.execute("UPDATE pages SET status='done', lastmod='d1'")
    second = parse_sitemap(sitemap_xml([(f"{BASE}/articles/1-a", "d1"), (f"{BASE}/articles/2-b", "d2"),
                                        (f"{BASE}/articles/4-d", "d1")]))
    diff = diff_against_manifest(conn, second)
    assert [e.article_id for e in diff.unchanged] == ["1"]
    assert [e.article_id for e in diff.changed] == ["2"]
    assert [e.article_id for e in diff.new] == ["4"]
    assert diff.removed == [f"{BASE}/articles/3-c"]


def test_section_chunks_carry_metadata_and_images():
    html = (FIXTURES / "article_with_images.html").read_text(encoding="utf-8")
    a = parse_article(html, f"{BASE}/articles/12000028489-x", "12000028489")
    chunks = chunk_article(a)
    assert chunks and all(c.title == a.title and c.url == a.url for c in chunks)
    assert sum(len(c.image_urls) for c in chunks) == 7
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    # Chunk ids are stable across runs (idempotent re-indexing).
    assert [c.chunk_id for c in chunk_article(a)] == [c.chunk_id for c in chunks]
