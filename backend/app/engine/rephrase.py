"""Optional Gemini rephrasing (spec M9). Rewrites the engine's verdict in plain,
calm language using only the engine output; returns None on any error so the
UI falls back to the template text."""

from __future__ import annotations

import json
import logging

import httpx

from app.config import settings
from app.models import Verdict

log = logging.getLogger(__name__)

PROMPT = """You rewrite emergency guidance for a member of the public.
Rules: use ONLY the facts in the JSON. Do not add new facts, numbers, places or advice.
Keep the verdict and every step. Never say "safe route"; say "least risky route in this data".
Write 2-4 short sentences, calm and direct, second person.

JSON:
{payload}"""


def rephrase(v: Verdict, timeout: float = 8.0) -> str | None:
    s = settings()
    payload = {
        "verdict": v.label, "reason": v.reason, "timeline": v.timeline, "steps": v.steps,
        "route": ({"destination": v.route.destination.name, "arrive_at": v.route.arrive_at.isoformat()}
                  if v.route else None),
        "assumptions": [a["text"] for a in v.assumptions],
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_model}:generateContent"
    try:
        r = httpx.post(url, params={"key": s.gemini_api_key}, timeout=timeout, json={
            "contents": [{"parts": [{"text": PROMPT.format(payload=json.dumps(payload, default=str))}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 300},
        })
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as ex:  # noqa: BLE001
        log.info("gemini rephrase skipped: %s", ex)
        return None
