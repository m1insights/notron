"""Nebius Token Factory client — the only place JUNO talks to a model.

Token Factory speaks the OpenAI protocol, so the official `openai` package
works unchanged against Nebius' endpoint. Every node in the graph picks a
model tier by job, not by habit: routing and guarding are constant background
work and run on Nano; planning and writing run on Super; deep research on Ultra.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/")

# Tier -> model id. Override any of these in .env, e.g. JUNO_MODEL_FAST=...
DEFAULT_MODELS = {
    "fast": "nvidia/nemotron-3-nano-30b-a3b",
    "smart": "nvidia/nemotron-3-super-120b-a12b",
    "deep": "nvidia/nemotron-3-ultra-550b-a32b",
}


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
    """No Nebius key configured. JUNO's hands still work; her head does not."""


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
                "create a key, then put it in apps/juno/.env"
            )
        return cls(api_key=key, base_url=os.environ.get("NEBIUS_BASE_URL", BASE_URL))

    def __post_init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)

    def model_for(self, tier: str) -> str:
        return os.environ.get(f"JUNO_MODEL_{tier.upper()}", DEFAULT_MODELS[tier])

    def available_models(self) -> list[str]:
        """Ask the account what it can actually run — model ids drift."""
        return sorted(m.id for m in self._client.models.list().data)

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
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(
            model=self.model_for(tier),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )
        return (resp.choices[0].message.content or "").strip()

    def ask_json(self, *, system: str, user: str, tier: str = "fast", **kw) -> dict:
        raw = self.ask(system=system, user=user, tier=tier, json_mode=True, **kw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                return json.loads(raw[start : end + 1])
            raise
