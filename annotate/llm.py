"""Shared LLM clients for OCR and annotation voters (free-tier APIs).

Keys are read from the repo-root .env (gitignored). Model chains handle
free-tier reality: 503 under demand → downshift; 429 → wait and retry.
All model names verified 2026-11-04.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_env() -> dict[str, str]:
    """Parse the repo-root .env file (KEY=VALUE lines, # comments)."""
    env: dict[str, str] = {}
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k.endswith("_API_KEY") and v})
    return env


#: newest → most reliable; newest flash models intermittently 503 on free tier
GEMINI_CHAIN = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
]

GROQ_DEFAULT = "openai/gpt-oss-120b"
#: OpenRouter free lineup rotates; pick at call time via :free filter if needed
OPENROUTER_DEFAULT = "nvidia/nemotron-3-ultra-550b-a55b:free"


def _post_json(url: str, body: dict, headers: dict, timeout: int = 120) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            # Cloudflare (Groq) returns 403/1010 for urllib's default UA
            "User-Agent": "urdu-ner-scraper/0.2 (+research; python-urllib)",
            "Accept": "application/json",
            "Content-Type": "application/json",
            **headers,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, {}


def _gemini_text(d: dict) -> str:
    parts = d.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


#: last model that returned 200 — tried first on the next call (sequential
#: runs otherwise re-pay the full downshift penalty on every request)
_preferred_model: str | None = None


def gemini_generate(
    parts: list[dict],
    *,
    env: dict[str, str] | None = None,
    chain: list[str] | None = None,
    temperature: float = 0.0,
    max_output_tokens: int = 4000,
    max_attempts_per_model: int = 2,
    thinking_budget: int | None = 0,
) -> tuple[str, str]:
    """Call Gemini with an inline-downshift chain. Returns (text, model_used).

    503/500 → try next model; 429 → sleep and retry same model.
    The last successful model is remembered and tried first next time.
    thinking_budget=0 disables Gemini 3.x hidden reasoning: for transcription
    and tagging it cut latency from ~120 s to ~5 s per batch.
    """
    global _preferred_model
    env = env or load_env()
    key = env.get("GOOGLE_API_KEY", "")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY missing from .env")
    chain = list(chain or GEMINI_CHAIN)
    if _preferred_model in chain:
        chain.remove(_preferred_model)
        chain.insert(0, _preferred_model)
    gen_config: dict = {"temperature": temperature, "maxOutputTokens": max_output_tokens}
    if thinking_budget is not None:
        gen_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    last_err = ""
    for model in chain:
        for attempt in range(max_attempts_per_model):
            status, d = _post_json(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                {
                    "contents": [{"parts": parts}],
                    "generationConfig": gen_config,
                },
                {"x-goog-api-key": key},
            )
            if status == 200 and "candidates" in d:
                _preferred_model = model
                return _gemini_text(d), model
            last_err = f"{model}: {status} {d.get('error', {}).get('message', '')[:80]}"
            if status == 429:
                # Free tier: "exceeded your current quota" is usually a daily
                # cap, not a per-minute one. Sleeping 20-40s per model wasted
                # ~3 minutes per batch before reaching the working model.
                time.sleep(2)
                break  # downshift now
            if status in (503, 500):  # high demand — brief pause then downshift
                time.sleep(2 * (attempt + 1))
                break
            if status in (400, 404):  # bad request / model gone — downshift now
                break
            time.sleep(2)
    raise RuntimeError(f"all Gemini models failed; last error: {last_err}")


def gemini_image_prompt(prompt: str, jpeg_bytes: bytes) -> list[dict]:
    """Build inline-image parts for gemini_generate."""
    return [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg_bytes).decode()}},
    ]


def groq_chat(messages: list[dict], *, model: str = GROQ_DEFAULT, env: dict[str, str] | None = None,
              temperature: float = 0.0, max_tokens: int = 2000) -> str:
    """Groq chat completion. gpt-oss models put the answer in message.content
    (reasoning arrives separately); we return content only."""
    env = env or load_env()
    key = env.get("GROQ_API_KEY", "")
    if not key:
        raise RuntimeError("GROQ_API_KEY missing from .env")
    status, d = _post_json(
        "https://api.groq.com/openai/v1/chat/completions",
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "reasoning_effort": "low",
        },
        {"Authorization": f"Bearer {key}"},
    )
    if status != 200:
        raise RuntimeError(f"groq {status}: {d.get('error', {}).get('message', '')[:150]}")
    return d["choices"][0]["message"].get("content", "") or ""


def openrouter_chat(messages: list[dict], *, model: str = OPENROUTER_DEFAULT,
                    env: dict[str, str] | None = None, temperature: float = 0.0,
                    max_tokens: int = 2000, reasoning_effort: str | None = "low") -> str:
    env = env or load_env()
    key = env.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY missing from .env")
    body: dict = {"model": model, "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens}
    if reasoning_effort:
        # reasoning models otherwise burn the whole token budget before
        # emitting the answer (nemotron: ~4K reasoning tokens -> truncated JSON)
        body["reasoning"] = {"effort": reasoning_effort}
    status, d = _post_json(
        "https://openrouter.ai/api/v1/chat/completions",
        body,
        {"Authorization": f"Bearer {key}"},
    )
    if status != 200:
        raise RuntimeError(f"openrouter {status}: {d.get('error', {}).get('message', '')[:150]}")
    choice = d["choices"][0]
    content = choice["message"].get("content", "") or ""
    if not content.strip():
        raise RuntimeError(f"openrouter empty content (finish={choice.get('finish_reason')})")
    return content
