"""Describe help-center screenshots with a Claude vision model."""

from __future__ import annotations

import base64
from pathlib import Path

from pydantic import BaseModel, Field

from common.llm import LLM, LLMResult
from common.prompts import PromptRegistry

PROMPTS = PromptRegistry(Path(__file__).resolve().parent.parent / "prompts")
SUPPORTED_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MIN_BYTES = 3_000  # icons and spacers are not worth describing
MAX_BYTES = 4_500_000  # API limit is 5 MB per image


class ImageDescription(BaseModel):
    description: str = Field(description="1-3 sentences: what the screenshot shows")
    ui_labels: list[str] = Field(description="Exact visible labels of menus, tabs, buttons, fields, settings")
    steps: list[str] = Field(description="Actions the screenshot illustrates, if any")
    uncertain: bool = Field(description="True if text is unreadable or the meaning is unclear")
    is_informative: bool = Field(description="False for decorative images, logos, or blank images")


class SkipImage(Exception):
    pass


def media_type_for(content_type: str, url: str) -> str:
    ct = content_type.split(";")[0].strip().lower()
    if ct in SUPPORTED_TYPES:
        return ct
    lower = url.lower().split("?")[0]
    for ext, mt in ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
                    (".gif", "image/gif"), (".webp", "image/webp")):
        if lower.endswith(ext):
            return mt
    raise SkipImage(f"unsupported image type {content_type!r}")


def describe_image_bytes(
    llm: LLM,
    data: bytes,
    content_type: str,
    url: str,
    *,
    article_title: str,
    section: str,
    context: str,
    model: str,
) -> LLMResult[ImageDescription]:
    if len(data) < MIN_BYTES:
        raise SkipImage("image too small")
    if len(data) > MAX_BYTES:
        raise SkipImage("image too large")
    media_type = media_type_for(content_type, url)
    prompt = PROMPTS.get("describe_image")
    text = (
        f"Article: {article_title}\nSection: {section or '(intro)'}\n"
        f"Text right before the screenshot: {context or '(none)'}\n\n"
        "Describe the screenshot."
    )
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                     "data": base64.standard_b64encode(data).decode("ascii")}},
        {"type": "text", "text": text},
    ]
    return llm.structured(
        schema=ImageDescription, system=prompt.system, user=content, model=model,
        purpose="describe_image", prompt_version=prompt.id, max_tokens=1500, effort="low",
    )


def description_to_text(d: ImageDescription) -> str:
    parts = [d.description]
    if d.ui_labels:
        parts.append("Visible labels: " + ", ".join(d.ui_labels))
    if d.steps:
        parts.append("Steps shown: " + "; ".join(d.steps))
    if d.uncertain:
        parts.append("(Note: parts of this screenshot were unclear.)")
    return " ".join(parts)
