"""Nebius Token Factory client — the only place NOTRON talks to a model.

Token Factory speaks the OpenAI protocol, so the official `openai` package
works unchanged against Nebius' endpoint. Every node in the graph picks a
model tier by job, not by habit: routing and guarding are constant background
work and run on Nano; planning and writing run on Super; deep research on Ultra.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

USAGE_LOG = Path(__file__).resolve().parents[1] / ".notron" / "usage.json"

BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")

# Tier -> model id. Override any of these in .env, e.g. NOTRON_MODEL_FAST=...
DEFAULT_MODELS = {
    "fast": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "smart": "nvidia/nemotron-3-super-120b-a12b",
    "deep": "nvidia/Nemotron-3-Ultra-550b-a55b",
}

# Nebius has no NVIDIA embedding model; all reasoning still runs on Nemotron.
EMBED_MODEL = "Qwen/Qwen3-Embedding-8B"

# Nor an NVIDIA vision model. This one read a screenshot correctly in 3.5s on
# 2026-09-05; `google/gemma-3-27b-it` is the fallback — cleaner output, 14.1s.
# Override with NOTRON_MODEL_VISION. Still Nebius, so the hackathon rule holds:
# every judgement about what a picture *means* is made by Nemotron afterwards.
VISION_MODEL = "openbmb/MiniCPM-V-4_5"

# Nemotron's chain of thought is billed against max_tokens but is not the answer.
# Every request gets this much extra room so the reply itself survives.
REASONING_HEADROOM = 1200


def _load_env() -> None:
    """Read a .env from the repo root without adding a dependency."""
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


class BrainUnavailable(RuntimeError):
    """No Nebius key configured. NOTRON's hands still work; her head does not."""


@dataclass
class Brain:
    api_key: str
    base_url: str = BASE_URL

    @classmethod
    def from_env(cls) -> "Brain":
        _load_env()
        key = os.environ.get("NEBIUS_API_KEY", "").strip()
        if not key:
            raise BrainUnavailable(
                "NEBIUS_API_KEY is not set. Sign up at https://tokenfactory.nebius.com, "
                "create a key, then put it in apps/notron/.env"
            )
        return cls(api_key=key, base_url=os.environ.get("NEBIUS_BASE_URL", BASE_URL))

    def __post_init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def _record(self, tier: str, usage) -> None:
        """Keep a running tally so Notron can report what she costs to run."""
        if usage is None:
            return
        try:
            USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
            data = json.loads(USAGE_LOG.read_text()) if USAGE_LOG.exists() else {}
            day = data.setdefault(str(date.today()), {})
            row = day.setdefault(tier, {"calls": 0, "in": 0, "out": 0})
            row["calls"] += 1
            row["in"] += getattr(usage, "prompt_tokens", 0) or 0
            row["out"] += getattr(usage, "completion_tokens", 0) or 0
            USAGE_LOG.write_text(json.dumps(data))
        except OSError:
            pass

    def model_for(self, tier: str) -> str:
        return os.environ.get(f"NOTRON_MODEL_{tier.upper()}", DEFAULT_MODELS[tier])

    def available_models(self) -> list[str]:
        """Ask the account what it can actually run — model ids drift."""
        return sorted(m.id for m in self._client.models.list().data)

    def _call(self, tier: str, system: str, user: str, budget: int, json_mode: bool,
              temperature: float):
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = self._client.chat.completions.create(
            model=self.model_for(tier),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=budget,
            temperature=temperature,
            **kwargs,
        )
        self._record(tier, getattr(resp, "usage", None))
        return resp.choices[0].message

    def ask(
        self,
        *,
        system: str,
        user: str,
        tier: str = "smart",
        json_mode: bool = False,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> str:
        """Ask a model for text.

        Nemotron reasons before it answers, and that reasoning is charged against
        the same token budget as the reply — so a budget sized only for the answer
        can come back completely empty. We add headroom for the thinking, and if the
        model still thinks itself out of room, we give it one bigger try.
        """
        budget = max_tokens + REASONING_HEADROOM
        msg = self._call(tier, system, user, budget, json_mode, temperature)
        content = (msg.content or "").strip()
        if content:
            return content

        if getattr(msg, "reasoning", None):
            msg = self._call(tier, system, user, budget * 2, json_mode, temperature)
            content = (msg.content or "").strip()
        return content

    def _look(self, model: str, question: str, data_uri: str, budget: int,
              temperature: float):
        resp = self._client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]}],
            max_tokens=budget,
            temperature=temperature,
        )
        self._record("vision", getattr(resp, "usage", None))
        return resp.choices[0].message

    def see(self, *, image: bytes, mime: str, question: str,
            max_tokens: int = 1200, temperature: float = 0.2) -> str:
        """Ask the vision model about one picture.

        Same shape as `ask`, and for the same reason: MiniCPM reasons in a
        <think> block billed against `max_tokens` exactly the way Nemotron
        does, and a 300-token budget came back 100% reasoning and 0% answer on
        the first live test. Headroom up front, and one bigger try if it still
        thinks itself out of room — but only when there really was thinking to
        blame, or an empty answer is bought twice at twice the price.
        """
        import base64

        model = os.environ.get("NOTRON_MODEL_VISION", VISION_MODEL)
        data_uri = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        budget = max_tokens + REASONING_HEADROOM

        msg = self._look(model, question, data_uri, budget, temperature)
        content = (msg.content or "").strip()
        if content:
            return content
        if getattr(msg, "reasoning", None):
            msg = self._look(model, question, data_uri, budget * 2, temperature)
            content = (msg.content or "").strip()
        return content

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Vectorise a batch of note chunks. Batches of ~64 keep requests small."""
        import os as _os

        model = _os.environ.get("NOTRON_MODEL_EMBED", EMBED_MODEL)
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            resp = self._client.embeddings.create(model=model, input=texts[i : i + 64])
            self._record("embed", getattr(resp, "usage", None))
            out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        return out

    def ask_json(self, *, system: str, user: str, tier: str = "fast", **kw) -> dict:
        """Parse a model's JSON, tolerating the three ways it usually goes wrong:
        prose wrapped around the object, a markdown fence, or a reply truncated
        mid-string by the token budget."""
        raw = self.ask(system=system, user=user, tier=tier, json_mode=True, **kw)
        for candidate in _json_candidates(raw):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        raise ValueError(f"model did not return usable JSON: {raw[:200]!r}")


def _json_candidates(raw: str):
    """Progressively more forgiving readings of a model's JSON reply."""
    yield raw
    fenced = raw.split("```")
    if len(fenced) >= 3:
        yield fenced[1].removeprefix("json").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        yield raw[start : end + 1]
    # Truncated mid-object: keep the complete key/value pairs and close it.
    if start >= 0:
        body = raw[start + 1 :]
        cut = body.rfind(",")
        while cut > 0:
            try:
                json.loads("{" + body[:cut] + "}")
                yield "{" + body[:cut] + "}"
                break
            except json.JSONDecodeError:
                cut = body.rfind(",", 0, cut)
