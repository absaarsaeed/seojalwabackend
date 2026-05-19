"""LLM service backed by OpenAI (api.openai.com).

Defaults to GPT-4o; the model is configurable per call.
Reads OPENAI_API_KEY from env (falls back to EMERGENT_LLM_KEY).
"""
import logging
import os
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger("jalwa.llm")


def _api_key() -> str:
    """Backward-compat sync path (used by the rare sync call site)."""
    return (os.environ.get("OPENAI_API_KEY")
            or os.environ.get("EMERGENT_LLM_KEY") or "")


async def _api_key_async() -> str:
    """Preferred async path — checks DB first via ConfigService."""
    from services.config import config_service
    key = await config_service.get_value("openai", "api_key")
    return key or _api_key()


async def chat_completion(
    system_message: str,
    user_text: str,
    model: str = "gpt-4o",
    provider: str = "openai",
    session_id: Optional[str] = None,
) -> str:
    """Single-turn completion using OpenAI."""
    try:
        key = await _api_key_async()
        if not key:
            raise RuntimeError("OPENAI_API_KEY missing")
        client = AsyncOpenAI(api_key=key)
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_text},
            ],
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.exception("LLM call failed: %s", e)
        # Graceful degradation so the API never crashes if the key is missing
        return f"[LLM unavailable: {e.__class__.__name__}]"


async def generate_article(
    topic: str,
    keyword: str,
    brand_voice: dict | None,
    length_words: int = 2000,
    instructions: str = "",
    language: str = "English",
    include_hero_image: bool = True,
    include_toc: bool = True,
    include_key_takeaways: bool = True,
    imagery_prompt: str = "",
) -> dict:
    """Real GPT-4o article generation. Returns a dict matching the spec.

    Output fields: title, metaDescription, content (HTML), excerpt,
    keyTakeaways[], faqSchema[{question,answer}], suggestedTags[],
    estimatedReadTime, wordCount, seoScore.
    """
    import json
    import re as _re

    voice_block = ""
    if brand_voice:
        sp = brand_voice.get("styleProfile") or brand_voice
        voice_block = (
            "\n\nBrand voice context:\n"
            f"- Tone: {sp.get('tone', 'balanced')}\n"
            f"- Formality: {sp.get('formality', sp.get('formalityScore', 50))}/100\n"
            f"- Playfulness: {sp.get('playfulness', sp.get('playfulnessScore', 50))}/100\n"
            f"- Technicality: {sp.get('technicality', sp.get('technicalityScore', 50))}/100\n"
            f"- Characteristic phrases to use: "
            f"{', '.join(sp.get('characteristicPhrases', []) or [])}\n"
            f"- Things to avoid: "
            f"{', '.join(sp.get('thingsToAvoid', []) or [])}"
        )

    system = (
        "You are an expert SEO content writer. Write a comprehensive, engaging "
        "article that ranks highly on Google. Follow these requirements exactly:\n\n"
        "Structure:\n"
        "- SEO-optimized title with target keyword near the beginning\n"
        "- Meta description 150-160 characters with keyword\n"
        "- H1 title (same as article title)\n"
        "- Introduction paragraph with keyword in first 100 words\n"
        "- 4-6 H2 subheadings with keyword variations\n"
        "- H3 subheadings under each H2 where appropriate\n"
        + ("- Key Takeaways box after introduction\n" if include_key_takeaways else "")
        + ("- Table of contents after key takeaways\n" if include_toc else "")
        + "- Conclusion with call to action\n"
        "- FAQ section with 5 questions (schema-friendly format)\n\n"
        "SEO requirements:\n"
        "- Target keyword density: 1-2%\n"
        "- LSI/related keywords throughout\n"
        "- Short sentences (under 20 words)\n"
        "- Short paragraphs (under 4 sentences)\n"
        "- Active voice throughout\n"
        "- External link suggestions marked as [EXTERNAL_LINK: anchor text]\n"
        "- Internal link suggestions marked as [INTERNAL_LINK: anchor text]\n\n"
        f"Language: {language}.\n"
        "Output format: Return STRICT JSON with these exact fields:\n"
        "{\n"
        '  "title": string,\n'
        '  "metaDescription": string,\n'
        '  "content": string (full HTML),\n'
        '  "excerpt": string (2 sentences),\n'
        '  "keyTakeaways": string[],\n'
        '  "faqSchema": [{"question": string, "answer": string}],\n'
        '  "suggestedTags": string[],\n'
        '  "estimatedReadTime": number,\n'
        '  "wordCount": number\n'
        "}\n"
        "Return ONLY the JSON object. No prose, no markdown fences."
        + voice_block
    )

    user_prompt = (
        f"Target topic: {topic}\n"
        f"Target keyword: {keyword}\n"
        f"Approximate word count: {length_words}\n"
        f"Extra instructions: {instructions or 'None'}\n"
    )

    raw = await chat_completion(system, user_prompt, model="gpt-4o")

    # Strip code fences if any then parse JSON
    cleaned = _re.sub(r"^```(?:json)?|```$", "", raw.strip(),
                      flags=_re.MULTILINE).strip()
    try:
        data = json.loads(cleaned)
    except Exception:
        match = _re.search(r"\{.*\}", cleaned, _re.S)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                data = {}
        else:
            data = {}

    # Defaults & sane fallbacks
    title = data.get("title") or topic
    content = data.get("content") or ""
    meta_description = data.get("metaDescription") or ""
    excerpt = data.get("excerpt") or ""
    key_takeaways = data.get("keyTakeaways") or []
    faq_schema = data.get("faqSchema") or []
    suggested_tags = data.get("suggestedTags") or []
    word_count = int(data.get("wordCount") or len(content.split()) or 0)
    read_time = int(data.get("estimatedReadTime") or max(1, word_count // 200))

    seo_score = calculate_seo_score(
        title=title, content=content, meta_description=meta_description,
        keyword=keyword, word_count=word_count,
        has_faq=bool(faq_schema), has_takeaways=bool(key_takeaways),
        has_toc=include_toc, read_time_set=bool(read_time),
    )

    return {
        "title": title,
        "metaDescription": meta_description,
        "metaTitle": title[:60],
        "content": content,
        "excerpt": excerpt,
        "keyTakeaways": key_takeaways,
        "faqSchema": faq_schema,
        "suggestedTags": suggested_tags,
        "estimatedReadTime": read_time,
        "wordCount": word_count,
        "seoScore": seo_score,
        "raw": raw if not data else None,
    }


def calculate_seo_score(*, title: str, content: str, meta_description: str,
                        keyword: str, word_count: int,
                        has_faq: bool, has_takeaways: bool,
                        has_toc: bool, read_time_set: bool) -> int:
    """Deterministic SEO scoring per spec (max 100)."""
    score = 0
    kw = (keyword or "").lower().strip()
    if not kw:
        return 0
    title_l = (title or "").lower()
    content_l = (content or "").lower()
    md = meta_description or ""
    first_100 = " ".join(content_l.split()[:100])

    if kw in title_l:
        score += 15
    if kw in first_100:
        score += 10
    if 150 <= len(md) <= 160:
        score += 10
    if kw in md.lower():
        score += 10
    h2_count = content_l.count("<h2")
    if h2_count >= 4:
        score += 15
    if word_count >= 1500:
        score += 10
    if has_faq:
        score += 10
    if has_takeaways:
        score += 10
    if has_toc:
        score += 5
    if read_time_set:
        score += 5
    return min(score, 100)


async def generate_hero_image(title: str, imagery_prompt: str = "") -> str | None:
    """Real DALL-E 3 hero image. Returns the OpenAI URL (caller re-uploads to R2)."""
    style = imagery_prompt or "modern professional business photography"
    prompt = (
        f"Professional blog header image for an article titled '{title}'. "
        f"Clean, modern, high-quality photography style. Bright and "
        f"professional. No text in the image. Aspect ratio 16:9. Style: {style}"
    )
    try:
        from openai import AsyncOpenAI
        key = await _api_key_async()
        client = AsyncOpenAI(api_key=key)
        resp = await client.images.generate(
            model="dall-e-3", prompt=prompt,
            size="1792x1024", quality="standard", n=1,
        )
        return resp.data[0].url
    except Exception as e:
        logger.warning("DALL-E hero gen failed: %s", e)
        return None


async def generate_social_caption(article_title: str, platform: str,
                                  brand_voice: dict | None = None) -> dict:
    system = (
        "You craft concise, native-feeling social posts. Output JSON-ish text: "
        "first line = caption (no hashtags), second line = comma-separated hashtags."
    )
    prompt = (
        f"Platform: {platform}\nArticle: {article_title}\n"
        "Write an engaging caption and 5 hashtags."
    )
    raw = await chat_completion(system, prompt)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    caption = lines[0] if lines else article_title
    hashtags = []
    if len(lines) > 1:
        hashtags = [h.strip().lstrip("#") for h in lines[1].split(",") if h.strip()]
    return {"caption": caption, "hashtags": hashtags}


async def analyse_brand_voice(samples: list[str]) -> dict:
    system = (
        "Analyse writing samples and return a brand voice profile in the form:\n"
        "FORMALITY:<0-100>\nPLAYFULNESS:<0-100>\nTECHNICALITY:<0-100>\n"
        "TONE: <one short sentence>\nVOCAB: <comma list of signature words>"
    )
    joined = "\n---\n".join(samples)[:6000]
    raw = await chat_completion(system, joined)

    def _extract(prefix: str, default=50):
        for line in raw.splitlines():
            if line.upper().startswith(prefix):
                val = line.split(":", 1)[1].strip()
                try:
                    return int(val.split()[0])
                except Exception:
                    return val
        return default

    return {
        "formalityScore": _extract("FORMALITY", 50),
        "playfulnessScore": _extract("PLAYFULNESS", 50),
        "technicalityScore": _extract("TECHNICALITY", 50),
        "tone": _extract("TONE", "balanced") if isinstance(
            _extract("TONE", "balanced"), str) else "balanced",
        "raw": raw,
    }


async def score_against_voice(content: str, brand_voice: dict | None) -> dict:
    system = (
        "Score the given text against the supplied brand voice and respond as:\n"
        "SCORE:<0-100>\nFEEDBACK:<one paragraph>"
    )
    prompt = f"Brand voice: {brand_voice}\n\nText:\n{content[:3000]}"
    raw = await chat_completion(system, prompt)
    score = 75
    feedback = raw
    for line in raw.splitlines():
        if line.upper().startswith("SCORE"):
            try:
                score = int("".join(c for c in line if c.isdigit())[:3])
            except Exception:
                pass
        elif line.upper().startswith("FEEDBACK"):
            feedback = line.split(":", 1)[1].strip()
    return {"score": min(max(score, 0), 100), "feedback": feedback}


# ---------- Image generation ----------
# DALL-E image generation via emergentintegrations is not exposed in this
# environment's LlmChat. We provide a clean interface and return a placeholder
# URL. TODO: replace with real DALL-E call when image API is available.
async def generate_image(prompt: str, size: str = "1024x1024") -> str:
    logger.info("Image gen requested: %s", prompt[:80])
    # TODO: when DALL-E 3 is wired up, call OpenAI Images API and upload to R2.
    seed = abs(hash(prompt)) % 100000
    return f"https://picsum.photos/seed/{seed}/1024/1024"
