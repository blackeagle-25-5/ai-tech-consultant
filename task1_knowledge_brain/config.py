"""Settings for the Knowledge Brain, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from common.llm import HAIKU

TASK_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("KB_DATA_DIR", TASK_DIR.parent / "data" / "task1"))


@dataclass(frozen=True)
class Settings:
    sitemap_url: str = os.getenv(
        "KB_SITEMAP_URL", "https://support.brilliantdirectories.com/support/sitemap.xml"
    )
    db_path: Path = field(default_factory=lambda: DATA_DIR / "knowledge.db")
    user_agent: str = "AITechConsultant-Capstone/0.1 (educational project; polite crawler)"
    crawl_concurrency: int = int(os.getenv("CRAWL_CONCURRENCY", "2"))
    crawl_delay_s: float = float(os.getenv("CRAWL_DELAY_S", "1.0"))
    max_attempts: int = 4
    request_timeout_s: float = 30.0
    max_images: int = int(os.getenv("MAX_IMAGES", "4000"))
    vision_model: str = os.getenv("KB_VISION_MODEL", HAIKU)
    answer_model: str = os.getenv("KB_ANSWER_MODEL", HAIKU)
    api_key: str = os.getenv("KB_API_KEY", "")
    # Retrieval: below this best-chunk similarity the bot abstains without calling Claude.
    # 0.65 calibrated on the eval set: answerable questions scored >= 0.706 (see EVALS.md).
    min_relevance: float = float(os.getenv("KB_MIN_RELEVANCE", "0.65"))
    top_k: int = 8


def get_settings() -> Settings:
    return Settings()
