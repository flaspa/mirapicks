"""Minimal Vultr Serverless Inference client (OpenAI-compatible chat completions)."""

from __future__ import annotations

import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1")


def chat_text(model: str, system: str, user: str, max_tokens: int = 12000) -> str:
    """Call a chat model and return its raw text reply."""
    key = os.environ["VULTR_INFERENCE_API_KEY"]
    resp = httpx.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.5,
            "max_tokens": max_tokens,
        },
        timeout=300,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"{model} inference failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()["choices"][0]["message"].get("content") or ""


def chat_json(model: str, system: str, user: str, max_tokens: int = 1500, reasoning_effort: str | None = None) -> dict:
    """Call a chat model and parse the first JSON object in its reply."""
    key = os.environ["VULTR_INFERENCE_API_KEY"]  # inference key only, never VULTR_API_KEY
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    if reasoning_effort:  # keeps reasoning models from spending the whole budget before answering
        body["reasoning_effort"] = reasoning_effort
    resp = httpx.post(f"{BASE_URL}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"{model} inference failed ({resp.status_code}): {resp.text[:300]}")
    content = resp.json()["choices"][0]["message"].get("content") or ""
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"{model} returned no JSON: {content[:300]}")
    return json.loads(content[start : end + 1])
