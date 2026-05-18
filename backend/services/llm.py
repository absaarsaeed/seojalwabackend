"""LLM service backed by Emergent Universal LLM Key (real OpenAI calls).

Real production calls use emergentintegrations.LlmChat. We default to GPT-4o
as specified, but the model is configurable.
"""
import logging
import os
import uuid
from typing import Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger("jalwa.llm")


def _api_key() -> str:
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        raise RuntimeError("EMERGENT_LLM_KEY missing in environment")
    return key


async def chat_completion(
    system_message: str,
    user_text: str,
    model: str = "gpt-4o",
    provider: str = "openai",
    session_id: Optional[str] = None,
) -> str:
    """Single-turn completion."""
    chat = LlmChat(
        api_key=_api_key(),
        session_id=session_id or str(uuid.uuid4()),
        system_message=system_message,
    ).with_model(provider, model)
    try:
        return await chat.send_message(UserMessage(text=user_text))
    except Exception as e:
        logger.exception("LLM call failed: %s", e)
        # Graceful degradation so the API never crashes if key is exhausted
        return f"[LLM unavailable: {e.__class__.__name__}]"


async def generate_article(
    topic: str,
    keyword: str,
    brand_voice: dict | None,
    length_words: int = 2000,
    instructions: str = "",
) -> dict:
    voice_note = ""
    if brand_voice:
        voice_note = (
            f"\nBrand voice — formality {brand_voice.get('formalityScore', 50)}/100, "
            f"playfulness {brand_voice.get('playfulnessScore', 50)}/100, "
            f"technicality {brand_voice.get('technicalityScore', 50)}/100."
        )
    system = (
        "You are an expert SEO content writer. Produce well-structured, "
        "engaging articles with H2/H3 headings, intro, key takeaways and conclusion."
        + voice_note
    )
    prompt = (
        f"Write an SEO article of about {length_words} words.\n"
        f"Title topic: {topic}\nTarget keyword: {keyword}\n"
        f"Extra instructions: {instructions or 'None'}\n"
        "Return the article as Markdown with a single H1 title at the top."
    )
    content = await chat_completion(system, prompt)
    title = topic
    for line in content.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    word_count = len(content.split())
    return {"title": title, "content": content, "wordCount": word_count}


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
