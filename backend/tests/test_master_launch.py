"""Quick unit tests for the Master-launch backend additions.

Run: cd /app/backend && python3 -m pytest tests/test_master_launch.py -v
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import asyncio  # noqa: E402
from services import llm  # noqa: E402


# ───────────────────────── pick_category ─────────────────────────
def test_pick_category_exact_match():
    mapping = {
        "How to brew coffee": {"id": 12, "name": "Coffee"},
        "Best travel destinations": {"id": 7, "name": "Travel"},
    }
    cat = llm.pick_category("How to brew coffee", mapping)
    assert cat == {"id": 12, "name": "Coffee"}


def test_pick_category_token_overlap():
    mapping = {
        "coffee brewing tips": {"id": 12, "name": "Coffee"},
        "travel destinations": {"id": 7, "name": "Travel"},
    }
    cat = llm.pick_category("Best coffee tools 2026", mapping)
    assert cat == {"id": 12, "name": "Coffee"}


def test_pick_category_no_match():
    mapping = {"coffee brewing tips": {"id": 12, "name": "Coffee"}}
    cat = llm.pick_category("How to fix a bicycle", mapping)
    assert cat is None


def test_pick_category_empty_mapping():
    assert llm.pick_category("anything", None) is None
    assert llm.pick_category("anything", {}) is None


# ───────────────────────── internal link resolution ─────────────────────────
def test_internal_link_resolves_to_existing_article():
    content = ("Read [INTERNAL_LINK: brewing coffee at home] for more, "
               "and check [INTERNAL_LINK: ufo lore in maine] too.")
    candidates = [
        {"id": "a1", "title": "The Ultimate Guide to Brewing Coffee at Home",
         "cmsUrl": "https://example.com/brewing-coffee",
         "slug": "brewing-coffee"},
        {"id": "a2", "title": "Hiking in Yosemite",
         "cmsUrl": "https://example.com/yosemite", "slug": "yosemite"},
    ]
    out = asyncio.get_event_loop().run_until_complete(
        llm.resolve_article_links(content, "topic", candidates))
    assert '<a href="https://example.com/brewing-coffee">brewing coffee at home</a>' in out
    # Unresolved placeholder becomes plain text (NOT the original placeholder)
    assert "[INTERNAL_LINK:" not in out
    assert "ufo lore in maine" in out


def test_internal_link_slug_fallback():
    content = "Try [INTERNAL_LINK: yoga basics] now."
    candidates = [{"id": "x", "title": "Yoga Basics for Beginners",
                   "cmsUrl": None, "slug": "yoga-basics-for-beginners"}]
    out = asyncio.get_event_loop().run_until_complete(
        llm.resolve_article_links(content, "topic", candidates))
    assert '<a href="/yoga-basics-for-beginners">yoga basics</a>' in out


def test_no_links_pass_through():
    content = "Plain paragraph with no placeholders."
    out = asyncio.get_event_loop().run_until_complete(
        llm.resolve_article_links(content, "topic", []))
    assert out == content


# ───────────────────────── _best_internal_match ─────────────────────────
def test_best_internal_match_picks_highest_overlap():
    candidates = [
        {"id": 1, "title": "Coffee Brewing Tips", "cmsUrl": "/a"},
        {"id": 2, "title": "Best Coffee Beans for Pour Over Brewing",
         "cmsUrl": "/b"},
    ]
    m = llm._best_internal_match("coffee pour over brewing", candidates)
    assert m["id"] == 2
