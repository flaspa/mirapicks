"""Mira's editorial assignment: "What are we covering?" (runs before Andy and Emily).

Same Mira (glm-5.3), acting at the start of the cycle. She sets topic, question and keywords only:
no source URLs, no searches, no FEATURE/WATCH/PASS.
"""

from __future__ import annotations

import os
import re
import uuid

from backend.vultr_inference import chat_json

MIRA_MODEL = os.getenv("MIRA_MODEL", "glm-5.3")

BEATS = [
    "runway couture",
    "streetwear / Gen Z style",
    "budget-friendly shopping",
    "emerging footwear",
    "sustainable fashion",
    "seasonal wardrobe shifts",
]

ASSIGNMENT_SYSTEM = f"""You are Mira, Editor-in-Chief of Mira Picks, a fashion publication.
Before any research happens, you set the editorial assignment your scouts will work from.
If the user gives a topic, use it. If not, choose ONE beat from: {", ".join(BEATS)}.
You may use the product context to make the assignment relevant, but do not judge the product yet.
Do not choose sources, do not search, and do not make any FEATURE/WATCH/PASS decision.
Reply with only a JSON object:
{{"topic": "the beat or user topic", "editorial_question": "one specific, answerable question",
  "angle": "one-sentence story angle to test", "keywords": ["3-5 short search keywords, most useful first"]}}"""


def fallback_assignment(topic: str | None) -> dict:
    t = (topic or "current fashion trends").strip()
    return {"assignment_id": uuid.uuid4().hex[:8], "topic": t,
            "editorial_question": f"What is actually gaining traction in {t} right now?",
            "angle": f"What the evidence says about {t} this season.", "keywords": [t], "source": "fallback"}


def make_assignment(topic: str | None = None, product_context: str | None = None) -> dict:
    """One Mira call; deterministic fallback on any failure. Never raises."""
    user = f"User topic: {topic.strip()}" if topic and topic.strip() else "User topic: (none, choose a beat)"
    if product_context:
        user += f"\nProduct context (for relevance only): {product_context}"
    try:
        a = chat_json(MIRA_MODEL, ASSIGNMENT_SYSTEM, user, max_tokens=4000)
        kws = [str(k).strip() for k in a.get("keywords", []) if str(k).strip()][:5]
        if not a.get("topic") or not a.get("editorial_question") or not kws:
            raise ValueError("incomplete assignment")
        return {"assignment_id": uuid.uuid4().hex[:8], "topic": str(a["topic"])[:80],
                "editorial_question": str(a["editorial_question"])[:300], "angle": str(a.get("angle") or "")[:300],
                "keywords": kws, "product_context": product_context, "source": "mira"}
    except Exception:
        return fallback_assignment(topic) | {"product_context": product_context}


def trend_query(assignment: dict) -> str:
    """One short, natural TikTok query: the lead keyword, plus the next one if the first is very short."""
    kws = assignment.get("keywords") or [assignment.get("topic") or "fashion trends"]
    q = kws[0]
    if len(q.split()) < 3 and len(kws) > 1:
        q = f"{q} {kws[1]}"
    words, seen = [], set()
    for w in re.sub(r"[^\w\s'-]", " ", q).split():
        if w.lower() not in seen:
            seen.add(w.lower())
            words.append(w)
    return " ".join(words[:6])
