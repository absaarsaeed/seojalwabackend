"""Real AI Visibility scanning — queries ChatGPT, Perplexity, Gemini, Claude.

When the real provider's API key is not configured (or the real call fails),
the model is simulated by GPT-4o using a persona system prompt so the scan
always returns 5 model scores. Each per-model result carries a
`simulated: bool` flag and a `note` so the UI can label simulated rows.

Copilot is always simulated (no public Microsoft API).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

import httpx

from services.llm import chat_completion, _api_key as openai_key  # noqa: F401

logger = logging.getLogger("jalwa.ai_visibility")


_SIMULATION_NOTE = "Simulated via GPT-4o (real key not configured)"


# ============================================================================
# GPT-4o simulation fallback — used when a model's real API key is missing
# ============================================================================
_MODEL_PERSONAS = {
    "chatgpt": "ChatGPT (OpenAI GPT-4o)",
    "perplexity": "Perplexity AI (the answer engine with cited web sources)",
    "gemini": "Google Gemini (Google's multimodal AI assistant)",
    "claude": "Anthropic Claude (a thoughtful, helpful assistant)",
    "copilot": "Microsoft Copilot (Bing-powered conversational AI)",
}


async def _simulate_via_gpt4o(model_name: str, query: str) -> str:
    """Use GPT-4o to roleplay how `model_name` would answer `query`."""
    persona = _MODEL_PERSONAS.get(model_name.lower(), model_name)
    sys = (
        f"You are simulating how {persona} would respond to questions "
        f"about brands. Answer as {persona} would, based on your knowledge "
        f"of how that AI system typically responds. Keep the answer concise "
        f"and mention specific businesses by name when relevant.")
    try:
        return await chat_completion(sys, query, model="gpt-4o")
    except Exception as e:
        logger.warning("gpt-4o simulation failed for %s: %s", model_name, e)
        return ""


# ---------------------------------------------------------------- Perplexity
async def _query_perplexity(query: str) -> str:
    from services.config import config_service
    api_key = (await config_service.get_value("perplexity", "api_key")
               or os.environ.get("PERPLEXITY_API_KEY", ""))
    if not api_key:
        return ""
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": "llama-3.1-sonar-small-128k-online",
                    "messages": [{"role": "user", "content": query}],
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning("perplexity error: %s", e)
        return ""


# -------------------------------------------------------------------- Gemini
async def _query_gemini(query: str) -> str:
    from services.config import config_service
    api_key = (await config_service.get_value("gemini", "api_key")
               or os.environ.get("GEMINI_API_KEY", ""))
    if not api_key:
        return ""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = await model.generate_content_async(query)
        return resp.text or ""
    except Exception as e:
        logger.warning("gemini error: %s", e)
        return ""


# -------------------------------------------------------------------- Claude
async def _query_claude(query: str) -> str:
    from services.config import config_service
    api_key = (await config_service.get_value("anthropic", "api_key")
               or os.environ.get("ANTHROPIC_API_KEY", ""))
    if not api_key:
        return ""
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=api_key)
        msg = await client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=512,
            messages=[{"role": "user", "content": query}],
        )
        # `content` is a list of blocks; join the text blocks
        return "".join(block.text for block in msg.content
                       if getattr(block, "type", "text") == "text")
    except Exception as e:
        logger.warning("claude error: %s", e)
        return ""


# ------------------------------------------------------------------- ChatGPT
async def _query_chatgpt(query: str) -> str:
    return await chat_completion(
        "Answer this question briefly, mentioning specific businesses you know of.",
        query,
        model="gpt-4o-mini",
    )


# ---------------------------------------------------- Brand-query generation
async def _generate_queries(site_url: str, site_name: str) -> list[str]:
    sys = "Return ONLY a JSON array of strings. No commentary."
    prompt = (
        f"Generate 20 search queries a customer would ask when looking for "
        f"a business like {site_name} ({site_url}). Mix informational, "
        f"comparison, and transactional queries.")
    raw = await chat_completion(sys, prompt, model="gpt-4o")
    # Extract JSON array even if model adds prose
    match = re.search(r"\[.*\]", raw, re.S)
    if match:
        try:
            arr = json.loads(match.group(0))
            return [str(q).strip() for q in arr if str(q).strip()][:20]
        except Exception:
            pass
    # Fallback deterministic queries — 25 to cover 5 models × 5 queries
    return [
        f"best alternatives to {site_name}",
        f"is {site_name} worth it",
        f"{site_name} vs competitors",
        f"how does {site_name} work",
        f"reviews of {site_name}",
    ] * 5


# ---------------------------------------------------------- Scoring & sentiment
def _detect_mention(text: str, site_name: str, site_url: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return site_name.lower() in t or site_url.lower().replace(
        "https://", "").replace("http://", "").split("/")[0] in t


def _sentiment(text: str, site_name: str) -> str:
    if not text:
        return "NOT_MENTIONED"
    t = text.lower()
    if site_name.lower() not in t:
        return "NOT_MENTIONED"
    positives = ["excellent", "great", "best", "leading", "recommended",
                 "innovative", "love", "top"]
    negatives = ["bad", "worst", "avoid", "issue", "problem", "expensive",
                 "complaint", "poor"]
    pos = sum(1 for w in positives if w in t)
    neg = sum(1 for w in negatives if w in t)
    if pos > neg:
        return "POSITIVE"
    if neg > pos:
        return "NEGATIVE"
    return "NEUTRAL"


async def _recommendations(site_url: str, results: dict) -> list[dict]:
    sys = ("Return ONLY a JSON array of objects with keys: action, difficulty"
           " (easy|medium|hard), expectedImpact (low|medium|high), category.")
    prompt = (
        f"Based on these AI visibility results for {site_url}: "
        f"{json.dumps(results)}\n"
        f"Give 5 specific, actionable recommendations to improve AI visibility."
    )
    raw = await chat_completion(sys, prompt, model="gpt-4o")
    match = re.search(r"\[.*\]", raw, re.S)
    if match:
        try:
            return json.loads(match.group(0))[:5]
        except Exception:
            pass
    return [
        {"action": "Publish an in-depth pillar page on your core topic",
         "difficulty": "medium", "expectedImpact": "high", "category": "content"},
        {"action": "Add FAQ schema to top landing pages",
         "difficulty": "easy", "expectedImpact": "medium", "category": "schema"},
        {"action": "Earn citations from authoritative sources (PR + guest posts)",
         "difficulty": "hard", "expectedImpact": "high", "category": "authority"},
        {"action": "Optimise meta descriptions for AI snippet eligibility",
         "difficulty": "easy", "expectedImpact": "medium", "category": "seo"},
        {"action": "Create comparison content vs the top 3 competitors",
         "difficulty": "medium", "expectedImpact": "high", "category": "content"},
    ]


# ------------------------------------------------------ Per-model scan runner
async def _real_or_simulated(model_name: str, real_fn,
                             query: str) -> tuple[str, bool]:
    """Returns (text, simulated). Falls back to GPT-4o simulation when the
    real provider returns empty (no key configured / error)."""
    if real_fn is not None:
        text = await real_fn(query)
        if text:
            return text, False
    # Either no real_fn (Copilot) or real call returned empty → simulate
    text = await _simulate_via_gpt4o(model_name, query)
    return text, True


async def _run_model_scan(name: str, real_fn, queries: list[str],
                          site_name: str, site_url: str) -> dict:
    # Defensive: if upstream supplied no queries for this model, fall back to
    # a generic prompt so the model still contributes a score.
    if not queries:
        queries = [f"What do you know about {site_name} ({site_url})?"]
    mentions = 0
    sentiments: list[str] = []
    any_simulated = False
    for q in queries[:5]:
        text, simulated = await _real_or_simulated(name, real_fn, q)
        if simulated:
            any_simulated = True
        if _detect_mention(text, site_name, site_url):
            mentions += 1
        sentiments.append(_sentiment(text, site_name))
    score = int((mentions / 5) * 100)
    # Dominant non-NOT_MENTIONED sentiment wins; otherwise NOT_MENTIONED
    counts = {s: sentiments.count(s) for s in set(sentiments)}
    counts.pop("NOT_MENTIONED", None)
    sentiment = max(counts, key=counts.get) if counts else "NOT_MENTIONED"
    out: dict = {"score": score, "sentiment": sentiment,
                 "mentions": mentions, "simulated": any_simulated}
    if any_simulated:
        out["note"] = _SIMULATION_NOTE
    return out


# =========================================================== PUBLIC API
async def run_scan(site: dict) -> dict:
    """Run the full 5-model AI visibility scan. Returns the score record."""
    site_url = site.get("url") or ""
    site_name = site.get("name") or site_url

    queries = await _generate_queries(site_url, site_name)
    # Split into 5 chunks of 5 (one per model)
    chunks = [queries[i * 5:(i + 1) * 5] for i in range(5)]
    while len(chunks) < 5:
        chunks.append(queries[:5])

    chatgpt = await _run_model_scan("chatgpt", _query_chatgpt, chunks[0],
                                    site_name, site_url)
    perplexity = await _run_model_scan("perplexity", _query_perplexity,
                                       chunks[1], site_name, site_url)
    gemini = await _run_model_scan("gemini", _query_gemini, chunks[2],
                                   site_name, site_url)
    claude = await _run_model_scan("claude", _query_claude, chunks[3],
                                   site_name, site_url)
    # Copilot has no public API — always simulated via GPT-4o
    copilot = await _run_model_scan("copilot", None, chunks[4],
                                    site_name, site_url)

    # Weighted overall: 30/25/20/15/10
    overall = int(round(
        chatgpt["score"] * 0.30
        + perplexity["score"] * 0.25
        + gemini["score"] * 0.20
        + claude["score"] * 0.15
        + copilot["score"] * 0.10
    ))

    results = {
        "chatgpt": chatgpt, "perplexity": perplexity, "gemini": gemini,
        "claude": claude, "copilot": copilot,
    }
    recs = await _recommendations(site_url, results)

    return {
        "overallScore": overall,
        "chatgptScore": chatgpt["score"],
        "perplexityScore": perplexity["score"],
        "geminiScore": gemini["score"],
        "claudeScore": claude["score"],
        "copilotScore": copilot["score"],
        "chatgptSentiment": chatgpt["sentiment"],
        "perplexitySentiment": perplexity["sentiment"],
        "geminiSentiment": gemini["sentiment"],
        "claudeSentiment": claude["sentiment"],
        "copilotSentiment": copilot["sentiment"],
        "recommendations": recs,
        "rawResults": results,
        "queries": queries,
    }


# ----------------------------------- service test (admin api-keys/test)
async def test_perplexity() -> dict:
    from services.config import config_service
    api_key = (await config_service.get_value("perplexity", "api_key")
               or os.environ.get("PERPLEXITY_API_KEY", ""))
    if not api_key:
        return {"success": False, "message": "PERPLEXITY_API_KEY not configured"}
    t = await _query_perplexity("Say READY")
    return {"success": bool(t), "message": (t or "no response")[:140]}


async def test_gemini() -> dict:
    from services.config import config_service
    api_key = (await config_service.get_value("gemini", "api_key")
               or os.environ.get("GEMINI_API_KEY", ""))
    if not api_key:
        return {"success": False, "message": "GEMINI_API_KEY not configured"}
    t = await _query_gemini("Say READY")
    return {"success": bool(t), "message": (t or "no response")[:140]}


async def test_anthropic() -> dict:
    from services.config import config_service
    api_key = (await config_service.get_value("anthropic", "api_key")
               or os.environ.get("ANTHROPIC_API_KEY", ""))
    if not api_key:
        return {"success": False, "message": "ANTHROPIC_API_KEY not configured"}
    t = await _query_claude("Say READY")
    return {"success": bool(t), "message": (t or "no response")[:140]}
