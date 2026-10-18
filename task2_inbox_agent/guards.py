"""Deterministic safety checks. They run on every email and every draft, whatever the model says."""

from __future__ import annotations

import re

INJECTION_PATTERNS = [
    r"ignore (all |any |your |the )?(previous |prior |above )?instructions",
    r"disregard (all |your |the )?(previous |prior )?(rules|instructions)",
    r"system prompt", r"you are now", r"act as (an? )?(admin|administrator|developer)",
    r"mark (this|the) (email|message) as approved", r"reveal (your|the) (prompt|instructions)",
]

# Commitments only a human may make.
REFUND_PROMISE = re.compile(
    r"\b(we|i)\s*(have|'ve|will|'ll)\s*(now\s+)?(issued|processed|refunded|credited|refund|credit|reverse[d]?)\b"
    r"|\brefund\s+(has|have)\s+been\s+(issued|processed|approved|sent)"
    r"|\byou\s+will\s+(receive|get)\s+(a\s+|your\s+)?(full\s+)?refund", re.I)
CANCEL_DONE = re.compile(
    r"\b(we|i)\s*(have|'ve)\s*(cancelled|canceled|closed|deleted)\b"
    r"|\b(subscription|account|plan)\s+(has|have)\s+been\s+(cancelled|canceled|closed|deleted)", re.I)
PRICE = re.compile(r"[$€£₹]\s?\d|\b\d+(?:[.,]\d+)?\s?(usd|eur|gbp|inr|dollars|euros|per hour|/hour|/hr)\b", re.I)
DEADLINE = re.compile(
    r"\b(by|within|before|in the next)\s+(today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|end of (the )?(day|week|month)|\d+\s*(hours?|hrs?|days?|business days?|weeks?))\b"
    r"|\bwill be (fixed|resolved|delivered|done|ready) (today|tomorrow|soon)\b", re.I)
CREDENTIALS = re.compile(r"\bpassword\s*(is|:)|\bhere('s| is) (your|the) (new )?password|reset[- ]link\s*:", re.I)
URL = re.compile(r"https?://[^\s)>\]]+", re.I)
EMAIL_ADDR = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")

ALLOWED_DOMAINS = ("support.brilliantdirectories.com",)


def detect_injection(text: str) -> bool:
    lower = text.lower()
    return any(re.search(p, lower) for p in INJECTION_PATTERNS)


def check_reply(reply: str, *, allowed_urls: list[str], evidence_text: str = "") -> list[str]:
    """Return a list of policy violations found in a draft reply (empty list = safe)."""
    violations = []
    if REFUND_PROMISE.search(reply):
        violations.append("refund_commitment")
    if CANCEL_DONE.search(reply):
        violations.append("cancellation_claimed_done")
    for m in PRICE.finditer(reply):
        if m.group(0) not in evidence_text:
            violations.append("price_not_in_evidence")
            break
    if DEADLINE.search(reply):
        violations.append("deadline_commitment")
    if CREDENTIALS.search(reply):
        violations.append("credentials_shared")
    allowed = set(allowed_urls)
    for url in URL.findall(reply):
        url = url.rstrip(".,;")
        if url not in allowed and not any(d in url for d in ALLOWED_DOMAINS):
            violations.append("unverified_link")
            break
    return violations


def wrap_untrusted(text: str) -> str:
    """Wrap customer text so the model treats it as data. Strips tags that could close the wrapper."""
    cleaned = re.sub(r"</?\s*(customer_email|instructions?|system)[^>]*>", "", text, flags=re.I)
    return f"<customer_email>\n{cleaned}\n</customer_email>"


def mask_pii(text: str) -> str:
    """For logs and audit entries: hide email addresses and phone numbers."""
    return PHONE.sub("[phone]", EMAIL_ADDR.sub("[email]", text))
