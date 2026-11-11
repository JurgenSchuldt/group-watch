"""Persona agents and the shared Bedrock client (with per-request cost metering).

Persona agents: one independent Bedrock call per viewer, role-playing that person's tastes. Used twice,
with models from DIFFERENT families so the evaluation is not circular:
  * agent.py   SCORER_MODEL (Claude Haiku 4.5)  each viewer's agent scores the shortlist -> the group pick
  * arena.py   JUDGE_MODEL  (Amazon Nova Pro)   each viewer's agent rates the final candidates of every system, blind

A persona sees ONLY its own profile and wish for tonight, never the other viewers.
Personas are LLM role-play from synthetic profiles: an estimate, not real viewer feedback.

Cost: every Bedrock call made through bedrock() is metered into the current Meter (see metered()),
priced with the on-demand us-east-1 list prices in PRICES. Models can be swapped with env vars.
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable, TypeVar

import boto3
from botocore.config import Config

BEDROCK_REGION = "us-east-1"  # Bedrock is denied in us-east-2
SMART_MODEL = os.environ.get("SMART_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")   # curator only
FAST_MODEL = os.environ.get("FAST_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")      # everything else
SCORER_MODEL = os.environ.get("BEDROCK_MODEL_ID", FAST_MODEL)
JUDGE_MODEL = os.environ.get("JUDGE_MODEL_ID", "us.amazon.nova-pro-v1:0")

# USD per 1M tokens (input, output), on-demand Standard tier, us-east-1, from the AWS Price List API
# (services AmazonBedrockFoundationModels and AmazonBedrock), checked 2026-11-04.
# "us." profiles are US Geo cross-Region inference (+10% over "global."). Prices change: re-check before quoting.
PRICES = {
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": (3.30, 16.50),
    "global.anthropic.claude-sonnet-4-5-20250929-v1:0": (3.00, 15.00),
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": (1.10, 5.50),
    "global.anthropic.claude-haiku-4-5-20251001-v1:0": (1.00, 5.00),
    "us.amazon.nova-pro-v1:0": (0.80, 3.20),
    "us.amazon.nova-lite-v1:0": (0.06, 0.24),
    "us.amazon.nova-micro-v1:0": (0.035, 0.14),
}


# ---------------------------------------------------------------- cost metering

class Meter:
    """Token and dollar totals for one request (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.by_model: dict[str, dict] = {}
        self.calls: list[dict] = []   # one row per Bedrock call: model, seconds, retries, stop reason, tokens

    def add(self, model: str, usage: dict, secs: float = 0.0, retries: int = 0, stop: str = "") -> None:
        with self._lock:
            self.calls.append({"model": model.split(".")[-1].split("-v")[0], "secs": round(secs, 1), "retries": retries,
                               "stop": stop, "out": usage.get("outputTokens", 0)})
            d = self.by_model.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            d["calls"] += 1
            d["input_tokens"] += usage.get("inputTokens", 0)
            d["output_tokens"] += usage.get("outputTokens", 0)

    def summary(self) -> dict:
        with self._lock:
            rows, total, unpriced = [], 0.0, []
            for model, d in self.by_model.items():
                p = PRICES.get(model)
                usd = (d["input_tokens"] * p[0] + d["output_tokens"] * p[1]) / 1e6 if p else None
                if usd is None:
                    unpriced.append(model)
                else:
                    total += usd
                rows.append({"model": model, **d, "usd": None if usd is None else round(usd, 5)})
            slow = sorted(self.calls, key=lambda c: -c["secs"])[:5]
            return {"usd": round(total, 5), "models": rows, "unpriced_models": unpriced,
                    "calls": sum(r["calls"] for r in rows), "slowest_calls": slow,
                    "retries": sum(c["retries"] for c in self.calls),
                    "truncated": sum(1 for c in self.calls if c["stop"] == "max_tokens")}


_meter: contextvars.ContextVar[Meter | None] = contextvars.ContextVar("bedrock_meter", default=None)


def metered() -> Meter:
    """Start metering Bedrock calls made in this context (and in pmap() workers started from it)."""
    m = Meter()
    _meter.set(m)
    return m


T = TypeVar("T")
R = TypeVar("R")


def pmap(fn: Callable[[T], R], items: Iterable[T]) -> list[R]:
    """Parallel map that carries the current context (so worker threads are metered too)."""
    items = list(items)
    if not items:
        return []
    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=len(items)) as ex:
        return list(ex.map(lambda x: ctx.copy().run(fn, x), items))


class _MeteredClient:
    def __init__(self, client) -> None:
        self._client = client

    def converse(self, **kwargs):
        t0 = time.monotonic()
        resp = self._client.converse(**kwargs)
        m = _meter.get()
        if m is not None:
            m.add(kwargs.get("modelId", "?"), resp.get("usage", {}), time.monotonic() - t0,
                  resp.get("ResponseMetadata", {}).get("RetryAttempts", 0), resp.get("stopReason", ""))
        return resp

    def __getattr__(self, name):
        return getattr(self._client, name)


_client = None
_client_lock = threading.Lock()
_last_used = 0.0
# Pooled HTTPS connections that sit idle get dropped silently by the network; reusing one made a 2-second
# call hang until the read timeout (seen live: 2 intent calls stuck >2 min after ~6 min idle). So: a fresh
# client after IDLE_REFRESH_S without calls, and a read timeout well above a normal call (Haiku scoring of
# 50 films ~12 s, Sonnet ~45 s) but far below the old 600 s, so a stalled call is retried instead of waited for.
IDLE_REFRESH_S = 45
READ_TIMEOUT_S = 90


def bedrock():
    """Shared, thread-safe Bedrock runtime client (one connection pool) with cost metering."""
    global _client, _last_used
    with _client_lock:
        now = time.monotonic()
        if _client is None or now - _last_used > IDLE_REFRESH_S:
            _client = _MeteredClient(boto3.client(
                "bedrock-runtime", region_name=BEDROCK_REGION,
                config=Config(connect_timeout=5, read_timeout=READ_TIMEOUT_S, tcp_keepalive=True,
                              retries={"max_attempts": 3, "mode": "standard"}, max_pool_connections=32)))
        _last_used = now
        return _client


# ---------------------------------------------------------------- persona agents

RATE_TOOL = {
    "name": "submit_ratings",
    "description": "Rate every option for this person.",
    "inputSchema": {"json": {
        "type": "object", "required": ["ratings"],
        "properties": {"ratings": {"type": "array", "items": {
            "type": "object", "required": ["option_id", "score", "would_watch", "dealbreaker", "reason"],
            "properties": {
                "option_id": {"type": "string"},
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "would_watch": {"type": "boolean"},
                "dealbreaker": {"type": "boolean", "description": "true ONLY if the film clearly has something you "
                                "explicitly refuse (dislikes, notes or tonight's wish). A dubbing or original-audio "
                                "preference lowers the score but is NOT a dealbreaker, unless you explicitly said you "
                                "cannot read subtitles. Never for films that are merely not your taste."},
                "reason": {"type": "string", "description": "At most 10 words, first person, about the synopsis."},
            }}}}}},
}
SYNOPSIS_CHARS = 320  # enough to judge fit; input tokens scale with this


def rate(viewer: dict, wish: str, options: list[dict], lang: str, model_id: str,
         max_tokens: int = 8000) -> dict[str, dict]:
    """One persona agent: rates every option as this person. `options` need option_id, title, genres, synopsis."""
    system = (
        f"You ARE {viewer.get('display_name', 'this viewer')}, a streaming viewer. Stay in character using only the "
        "profile below. You are choosing a film to watch tonight and you have not seen these films. Judge each "
        "option ONLY from its synopsis, genres and rating against your tastes, languages, dislikes and what you "
        "want tonight. Be honest and differentiated (0-100): a film you would turn off should score under 30, and "
        "only a film you would really enjoy tonight should score over 70. Mark dealbreaker only for explicit refusals; "
        "popular foreign films usually have dubbed and subtitled versions. "
        f"Write reasons in language code '{lang}', first person, at most 10 words. "
        "Rate EVERY option exactly once.\n\nYour profile:\n"
        + json.dumps(viewer, ensure_ascii=False, separators=(",", ":")) + (f"\n\nWhat you want tonight: {wish}" if wish else ""))

    def payload(opts: list[dict]) -> str:
        return json.dumps([{"option_id": o["option_id"], "title": o["title"], "year": o.get("year"), "genres": o["genres"],
                            "director": o.get("director"), "runtime_min": o.get("runtime_min"),
                            "imdb_rating": o.get("imdb_rating"), "original_language": o.get("original_language"),
                            "synopsis": o["synopsis"][:SYNOPSIS_CHARS]} for o in opts], ensure_ascii=False, separators=(",", ":"))

    got: dict[str, dict] = {}
    todo = list(options)
    for _ in range(3):  # models sometimes skip an option: ask again for just the missing ones
        resp = bedrock().converse(
            modelId=model_id, system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": payload(todo)}]}],
            toolConfig={"tools": [{"toolSpec": RATE_TOOL}], "toolChoice": {"tool": {"name": RATE_TOOL["name"]}}},
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.3})
        wanted = {o["option_id"] for o in todo}
        for block in resp["output"]["message"]["content"]:
            if "toolUse" in block:
                for r in block["toolUse"]["input"].get("ratings", []):
                    if isinstance(r, dict) and isinstance(r.get("score"), int) and r.get("option_id") in wanted:
                        got[r["option_id"]] = r
        todo = [o for o in options if o["option_id"] not in got]
        if not todo:
            return got
    raise RuntimeError(f"persona {viewer.get('display_name')} left {len(todo)} option(s) unrated")
