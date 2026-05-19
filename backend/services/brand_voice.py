"""Real brand voice training — fetch a URL, extract visible text, GPT-4o profile."""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import httpx

from services.llm import chat_completion

logger = logging.getLogger("jalwa.brand_voice")


async def fetch_visible_text(url: str, max_chars: int = 8000) -> str:
    """Fetch a URL and return visible text only, capped at max_chars."""
    if not url.startswith("http"):
        url = f"https://{url}"
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(url, headers={
                "User-Agent": "SEOJalwaBot/1.0 (+https://seojalwa.com)"})
            r.raise_for_status()
            html = r.text
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
        except Exception:
            # Fallback regex strip
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        logger.warning("brand_voice fetch failed for %s: %s", url, e)
        return ""


async def analyse_profile(samples: list[str]) -> dict:
    sys = (
        "Analyze the writing samples and return a JSON object with exactly "
        "these keys:\n"
        "tone (string), formality (0-100), playfulness (0-100), "
        "technicality (0-100), sentenceLength ('short'|'medium'|'long'), "
        "vocabulary ('simple'|'moderate'|'advanced'), "
        "characteristicPhrases (array of strings), "
        "thingsToAvoid (array of strings), writingPersona (string).\n"
        "Output ONLY the JSON, no commentary.")
    joined = "\n---\n".join(samples)[:9000]
    raw = await chat_completion(sys, joined, model="gpt-4o")
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return _fallback_profile()
    try:
        profile = json.loads(match.group(0))
    except Exception:
        return _fallback_profile()

    # Ensure required fields & sane defaults
    for key, default in (
        ("tone", "balanced"), ("formality", 50), ("playfulness", 50),
        ("technicality", 50), ("sentenceLength", "medium"),
        ("vocabulary", "moderate"), ("characteristicPhrases", []),
        ("thingsToAvoid", []), ("writingPersona", "professional friendly"),
    ):
        profile.setdefault(key, default)
    return profile


def _fallback_profile() -> dict:
    return {
        "tone": "balanced",
        "formality": 50, "playfulness": 50, "technicality": 50,
        "sentenceLength": "medium", "vocabulary": "moderate",
        "characteristicPhrases": [],
        "thingsToAvoid": [],
        "writingPersona": "professional friendly",
    }


async def score_against_profile(content: str, profile: dict) -> dict:
    sys = ("Return ONLY JSON: {\"score\": 0-100, \"feedback\": \"...\"}. "
           "Score how well the content matches the brand voice profile.")
    prompt = (f"Brand voice profile: {json.dumps(profile)}\n\n"
              f"Content:\n{content[:3000]}")
    raw = await chat_completion(sys, prompt, model="gpt-4o")
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        return {"score": 70, "feedback": raw[:300]}
    try:
        out = json.loads(match.group(0))
        return {"score": int(out.get("score", 70)),
                "feedback": str(out.get("feedback", ""))}
    except Exception:
        return {"score": 70, "feedback": raw[:300]}
