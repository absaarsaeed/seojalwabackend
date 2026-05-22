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
    """Simplified ChatGPT-only AI visibility scan.

    Runs 5 brand-discovery queries through GPT-4o and computes a simple
    visibility score based on how many responses mention the site by name
    or domain. Generates 3-5 plain-English recommendations.
    """
    site_url = site.get("url") or ""
    site_name = site.get("name") or site_url

    queries = [
        f"What is {site_name}?",
        f"Tell me about {site_name}",
        f"Is {site_name} a good website?",
        f"What does {site_name} offer?",
        f"Reviews of {site_name}",
    ]

    results: list[dict] = []
    mention_count = 0
    for q in queries:
        try:
            answer = await chat_completion(
                "Answer concisely. Mention specific businesses by name.",
                q, model="gpt-4o")
        except Exception as e:
            logger.warning("ChatGPT query failed: %s", e)
            results.append({"query": q, "mentioned": False,
                             "error": str(e)[:160], "response_snippet": ""})
            continue
        mentioned = _detect_mention(answer, site_name, site_url)
        if mentioned:
            mention_count += 1
        results.append({
            "query": q, "mentioned": mentioned,
            "response_snippet": (answer or "")[:200],
        })

    score = int((mention_count / len(queries)) * 100)
    if score >= 60:
        status = "VISIBLE"
        message = f"{site_name} is visible on AI chat engines"
    elif score >= 20:
        status = "PARTIAL"
        message = f"{site_name} is partially visible on AI chat engines"
    else:
        status = "NOT_VISIBLE"
        message = f"{site_name} is not yet visible on AI chat engines"

    # Recommendations — single GPT-4o call, JSON output
    rec_sys = ("Reply ONLY with a JSON array. Each item has keys: title, "
               "description, difficulty (easy|medium|hard), impact "
               "(low|medium|high).")
    rec_prompt = (
        f"Website: {site_name} ({site_url})\n"
        f"AI Visibility Score: {score}/100\n"
        f"Status: {status}\n"
        f"Queries run: {len(queries)}, mentions found: {mention_count}\n\n"
        f"Give 3-5 specific recommendations to improve AI visibility.")
    try:
        raw = await chat_completion(rec_sys, rec_prompt, model="gpt-4o")
        match = re.search(r"\[.*\]", raw, re.S)
        recommendations = json.loads(match.group(0))[:5] if match else []
    except Exception as e:
        logger.warning("recommendations gen failed: %s", e)
        recommendations = [
            {"title": "Publish branded pillar content",
             "description": ("Long-form articles about who you are and what "
                              "you do help AIs learn your brand."),
             "difficulty": "medium", "impact": "high"},
            {"title": "Get listed in industry roundups",
             "description": ("Earn citations from authoritative sources to "
                              "boost AI training signal."),
             "difficulty": "hard", "impact": "high"},
            {"title": "Add FAQ schema to key pages",
             "description": "Structured data helps AIs extract clean answers.",
             "difficulty": "easy", "impact": "medium"},
        ]

    # Maintain the legacy 5-model shape for any UI that still reads it —
    # all four extra models report score 0 / NOT_MENTIONED so the UI can
    # collapse them gracefully. The simplified UI should read
    # `overallScore`, `visibilityStatus`, `visibilityMessage`, `results`.
    zero_model = {"score": 0, "sentiment": "NOT_MENTIONED", "mentions": 0,
                  "simulated": False}
    return {
        "overallScore": score,
        "visibilityStatus": status,
        "visibilityMessage": message,
        "queriesRun": len(queries),
        "mentionsFound": mention_count,
        "results": results,
        "queries": queries,
        "recommendations": recommendations,
        # Legacy/back-compat fields
        "chatgptScore": score,
        "perplexityScore": 0, "geminiScore": 0, "claudeScore": 0,
        "copilotScore": 0,
        "chatgptSentiment": "POSITIVE" if score >= 60
            else "NEUTRAL" if score >= 20 else "NOT_MENTIONED",
        "perplexitySentiment": "NOT_MENTIONED",
        "geminiSentiment": "NOT_MENTIONED",
        "claudeSentiment": "NOT_MENTIONED",
        "copilotSentiment": "NOT_MENTIONED",
        "rawResults": {"chatgpt": {**zero_model, "score": score,
                                     "mentions": mention_count}},
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
