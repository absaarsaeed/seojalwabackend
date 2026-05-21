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
# Legacy placeholder used by social-post generation. Real DALL-E 3 hero images
# are produced via `generate_hero_image` above using the openai package.
async def generate_image(prompt: str, size: str = "1024x1024") -> str:
    logger.info("Image gen requested: %s", prompt[:80])
    seed = abs(hash(prompt)) % 100000
    return f"https://picsum.photos/seed/{seed}/1024/1024"


# ---------- Internal / External link resolution (Master prompt part 5) ----------
import re as _re_links


def _best_internal_match(anchor: str, candidates: list[dict]) -> dict | None:
    """Return the candidate article whose title best matches the anchor.

    Pure token-overlap scoring — fast and deterministic. `candidates` is a list
    of `{id, title, cmsUrl, slug}` dicts.
    """
    if not candidates:
        return None
    anchor_tokens = {w for w in _re_links.findall(r"[a-z0-9]+",
                                                   anchor.lower()) if len(w) > 2}
    if not anchor_tokens:
        return None
    best: dict | None = None
    best_score = 0
    for c in candidates:
        title_tokens = {w for w in _re_links.findall(
            r"[a-z0-9]+", (c.get("title") or "").lower()) if len(w) > 2}
        if not title_tokens:
            continue
        overlap = len(anchor_tokens & title_tokens)
        if overlap > best_score:
            best_score = overlap
            best = c
    return best if best_score >= 1 else None


async def _suggest_external_url(anchor: str, topic: str) -> str | None:
    """Ask GPT for ONE authoritative external URL for the anchor text.

    Returns a URL string or None. The model is restricted to reputable
    domains (wikipedia, .gov, .edu, major news/industry sites).
    """
    system = (
        "Reply with ONE real, authoritative URL (https) supporting the "
        "anchor text. Prefer wikipedia.org, .gov, .edu, or top industry "
        "sources. No commentary, no markdown. If unsure, reply NONE."
    )
    prompt = f"Topic: {topic}\nAnchor text: {anchor}"
    try:
        raw = (await chat_completion(system, prompt)).strip()
        if not raw or raw.upper().startswith("NONE"):
            return None
        url = raw.split()[0].strip("<>`\"' ")
        if url.startswith("http"):
            return url
    except Exception:
        return None
    return None


_INT_LINK_RE = _re_links.compile(r"\[INTERNAL_LINK:\s*([^\]]+)\]")
_EXT_LINK_RE = _re_links.compile(r"\[EXTERNAL_LINK:\s*([^\]]+)\]")


async def resolve_article_links(content: str, topic: str,
                                 internal_candidates: list[dict]) -> str:
    """Replace `[INTERNAL_LINK: anchor]` and `[EXTERNAL_LINK: anchor]`
    placeholders with real `<a>` tags. Unresolved placeholders are stripped
    to plain anchor text so the published article is always clean.
    """
    if not content:
        return content or ""

    # 1) Internal links
    def _resolve_internal(m: "_re_links.Match[str]") -> str:
        anchor = m.group(1).strip()
        match = _best_internal_match(anchor, internal_candidates)
        if not match:
            return anchor  # drop the placeholder, keep the words
        href = (match.get("cmsUrl")
                or (f"/{match['slug']}" if match.get("slug") else ""))
        if not href:
            return anchor
        return f'<a href="{href}">{anchor}</a>'

    content = _INT_LINK_RE.sub(_resolve_internal, content)

    # 2) External links — fetch URLs in parallel (cap at 5 to limit cost)
    ext_matches = list(_EXT_LINK_RE.finditer(content))
    if ext_matches:
        import asyncio as _aio
        anchors = [m.group(1).strip() for m in ext_matches[:5]]
        urls = await _aio.gather(
            *[_suggest_external_url(a, topic) for a in anchors],
            return_exceptions=True)
        url_map: dict[str, str | None] = {}
        for a, u in zip(anchors, urls):
            url_map[a] = u if isinstance(u, str) else None

        def _resolve_external(m: "_re_links.Match[str]") -> str:
            anchor = m.group(1).strip()
            url = url_map.get(anchor)
            if not url:
                return anchor
            return (f'<a href="{url}" rel="nofollow noopener" '
                    f'target="_blank">{anchor}</a>')

        content = _EXT_LINK_RE.sub(_resolve_external, content)

    return content


def pick_category(topic: str,
                   category_mapping: dict | None) -> dict | None:
    """Pick the best matching WordPress category for the article topic.

    `category_mapping` is shaped `{topic_label: {"id": <int>, "name": <str>}}`
    (built by site_analyzer). Falls back to substring match if no perfect hit.
    Returns the matched value dict or None.
    """
    if not category_mapping:
        return None
    topic_l = (topic or "").lower()
    # Exact / startswith match first
    for label, val in category_mapping.items():
        if not val:
            continue
        if topic_l == (label or "").lower():
            return val
    # Token overlap match
    topic_tokens = {w for w in _re_links.findall(r"[a-z0-9]+", topic_l)
                    if len(w) > 2}
    best = None
    best_score = 0
    for label, val in category_mapping.items():
        if not val:
            continue
        label_tokens = {w for w in _re_links.findall(
            r"[a-z0-9]+", (label or "").lower()) if len(w) > 2}
        overlap = len(topic_tokens & label_tokens)
        if overlap > best_score:
            best_score = overlap
            best = val
    return best if best_score >= 1 else None
