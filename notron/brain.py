"""Nebius Token Factory client — the only place NOTRON talks to a model.

Token Factory speaks the OpenAI protocol, so the official `openai` package
works unchanged against Nebius' endpoint. Every node in the graph picks a
model tier by job, not by habit: routing and guarding are constant background
work and run on Nano; planning and writing run on Super; deep research on Ultra.
"""

from __future__ import annotations

import json
import os
import re
import random
import signal
import threading
import time
import math
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Sequence

from .outbound import Passage, Purpose, prepare_outbound
from . import network

from .paths import DATA_DIR
USAGE_LOG = DATA_DIR / "usage.json"
PROVIDER_STATE = DATA_DIR / "provider.json"

INTERACTIVE_DEADLINE = 30.0
BATCH_DEADLINE = 60.0
MAX_ATTEMPTS = 2
_deadline_seconds: ContextVar[float] = ContextVar('brain_deadline_seconds', default=INTERACTIVE_DEADLINE)


@contextmanager
def batch_deadline():
    """Give filing/index work a 60-second total provider-call budget."""
    token = _deadline_seconds.set(BATCH_DEADLINE)
    try:
        yield
    finally:
        _deadline_seconds.reset(token)


@contextmanager
def _deadline_guard(deadline: float):
    """Enforce the absolute deadline around blocking DNS on the main thread."""
    with network.deadline(deadline):
        if threading.current_thread() is not threading.main_thread():
            raise network.ProviderDeadlineError(
                'Provider calls require the deadline-capable main worker thread.')
        def expired(signum, frame):
            raise network.ProviderDeadlineError('Provider did not answer before its deadline.')

        previous = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, expired)
        started = time.monotonic()
        old_timer = signal.setitimer(signal.ITIMER_REAL, max(0.001, deadline - time.monotonic()))
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            if old_timer[0] > 0:
                elapsed = time.monotonic() - started
                signal.setitimer(signal.ITIMER_REAL, max(0.001, old_timer[0] - elapsed), old_timer[1])


_PROVIDERS = ('nebius', 'tavily')


def _empty_retry_state() -> dict:
    return {service: {'failures': 0, 'retry_after': 0.0} for service in _PROVIDERS}


def _all_retry_state() -> dict:
    if PROVIDER_STATE.is_symlink() or PROVIDER_STATE.parent.is_symlink():
        raise network.ProviderStateError('Provider retry state is unavailable.')
    try:
        raw = json.loads(PROVIDER_STATE.read_text())
    except FileNotFoundError:
        return _empty_retry_state()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        raise network.ProviderStateError('Provider retry state is unavailable.') from None
    if not isinstance(raw, dict) or set(raw) != set(_PROVIDERS):
        raise network.ProviderStateError('Provider retry state is unavailable.')
    for service in _PROVIDERS:
        row = raw[service]
        if (not isinstance(row, dict) or set(row) != {'failures', 'retry_after'}
                or isinstance(row.get('failures'), bool)
                or not isinstance(row.get('failures'), int) or row['failures'] < 0
                or isinstance(row.get('retry_after'), bool)
                or not isinstance(row.get('retry_after'), (int, float))
                or not math.isfinite(row['retry_after']) or row['retry_after'] < 0):
            raise network.ProviderStateError('Provider retry state is unavailable.')
        row['retry_after'] = float(row['retry_after'])
    return raw


def _retry_state(service: str = 'nebius') -> dict:
    if service not in _PROVIDERS:
        raise network.ProviderStateError('Provider retry state is unavailable.')
    return _all_retry_state()[service]


def _save_retry_state(service: str, failures: int, retry_after: float) -> None:
    from .persistence import atomic_write_json
    from .securestore import private_directory
    PROVIDER_STATE.parent.mkdir(parents=True, exist_ok=True)
    private_directory(PROVIDER_STATE.parent)
    state = _all_retry_state()
    state[service] = {'failures': failures, 'retry_after': retry_after}
    atomic_write_json(PROVIDER_STATE, state)


def _check_cooldown(service: str = 'nebius') -> None:
    if _retry_state(service)['retry_after'] > time.time():
        raise network.ProviderCooldownError('Provider is temporarily cooling down.')


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise network.ProviderDeadlineError('Provider did not answer before its deadline.')
    return left


def _status_failure(error: Exception) -> Exception:
    from openai import APIStatusError
    import httpx2
    if not isinstance(error, (APIStatusError, httpx2.HTTPStatusError)):
        return error
    status = (error.response.status_code if isinstance(error, httpx2.HTTPStatusError)
              else error.status_code)
    if status in (408, 409, 429) or 500 <= status <= 599:
        return network.ProviderConnectivityError('Provider is temporarily unavailable.')
    return network.ProviderRejectedError(status)


def provider_call(operation, deadline: float, *, service: str, attempts: int = MAX_ATTEMPTS,
                  clear_on_success: bool = True):
    """Run one safe provider operation under bounded retry and cooldown rules."""
    for attempt in range(attempts):
        try:
            result = operation()
            if clear_on_success:
                _recovered(service)
            return result
        except network.ProviderCooldownError:
            raise
        except network.ProviderDeadlineError:
            _failed(service)
            raise
        except network.ProviderConnectivityError as error:
            failure = error
        except Exception as error:
            failure = _status_failure(error)
        if isinstance(failure, network.ProviderRejectedError):
            raise failure from None
        if isinstance(failure, network.ProviderConnectivityError):
            if attempt + 1 >= attempts:
                _failed(service)
                raise failure from None
            delay = random.uniform(0.2, 0.5)
            if delay >= _remaining(deadline):
                _failed(service)
                raise network.ProviderDeadlineError(
                    'Provider did not answer before its deadline.') from None
            time.sleep(delay)
            continue
        raise failure
    raise AssertionError('unreachable')


def _failed(service: str = 'nebius') -> None:
    state = _retry_state(service)
    failures = state['failures'] + 1
    delay = min(60.0, 2.0 ** min(failures - 1, 5))
    _save_retry_state(service, failures, time.time() + delay)


def _recovered(service: str = 'nebius') -> None:
    if _retry_state(service)['failures']:
        _save_retry_state(service, 0, 0.0)

BASE_URL = network.NEBIUS_URL

# Tier -> model id. Non-secret configuration overrides remain environment settings.
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

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_UNCLOSED_THINK = re.compile(r"<think>.*\Z", re.S | re.I)

# Nemotron's chain of thought is billed against max_tokens but is not the answer.
# Every request gets this much extra room so the reply itself survives.
REASONING_HEADROOM = 1200


class BrainUnavailable(RuntimeError):
    """Compatibility error for unavailable inference configuration."""


@dataclass
class Brain:
    api_key: str = field(repr=False)
    base_url: str = BASE_URL
    transport: object | None = field(default=None,repr=False)

    @classmethod
    def from_credentials(cls) -> "Brain":
        from . import credentials, retention
        retention.require_ready()
        from .transport import configured,bootstrap_inherited
        bootstrap_inherited()
        if configured() is not None: return cls(api_key="",transport=configured())
        endpoint = network.provider_endpoint(os.environ.get("NEBIUS_BASE_URL", BASE_URL), 'nebius')
        key = credentials.require(endpoint.credential_name).decode('utf-8')
        return cls(api_key=key, base_url=endpoint.url)

    def __post_init__(self) -> None:
        from openai import OpenAI

        from . import credentials, retention
        retention.require_ready()
        if self.transport is not None:
            return
        self._endpoint = network.provider_endpoint(self.base_url, 'nebius')
        # Constructor-supplied keys never bypass the injected credential contract.
        self.api_key = credentials.require(self._endpoint.credential_name).decode('utf-8')
        self._client = OpenAI(base_url=self._endpoint.url, api_key=self.api_key,
                              http_client=network.provider_client(self._endpoint), max_retries=0)

    def _record(self, tier: str, usage) -> None:
        """Keep a running tally so Notron can report what she costs to run."""
        if usage is None:
            return
        try:
            USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
            from .diagnostics import prune_usage
            data = prune_usage(USAGE_LOG)
            day = data.setdefault(str(date.today()), {})
            row = day.setdefault(tier, {"calls": 0, "in": 0, "out": 0})
            row["calls"] += 1
            row["in"] += getattr(usage, "prompt_tokens", 0) or 0
            row["out"] += getattr(usage, "completion_tokens", 0) or 0
            from .persistence import atomic_write_json
            from .securestore import private_directory
            private_directory(USAGE_LOG.parent)
            atomic_write_json(USAGE_LOG, data)
        except OSError:
            pass

    def model_for(self, tier: str) -> str:
        return os.environ.get(f"NOTRON_MODEL_{tier.upper()}", DEFAULT_MODELS[tier])

    def _check_credentials(self) -> None:
        from . import credentials, retention
        retention.require_ready()
        if getattr(self,'transport',None) is not None: return
        endpoint = getattr(self, '_endpoint', None) or network.provider_endpoint(self.base_url, 'nebius')
        key = credentials.require(endpoint.credential_name).decode('utf-8')
        # Check on each transport, including retries and embedding batches.
        self._client.api_key = key

    def available_models(self) -> list[str]:
        """Ask the account what it can actually run — model ids drift."""
        if getattr(self,"transport",None) is not None: return sorted(DEFAULT_MODELS.values())
        deadline = time.monotonic() + INTERACTIVE_DEADLINE
        with _deadline_guard(deadline):
            if getattr(self,"transport",None) is None: _check_cooldown()
            self._check_credentials()
            try:
                response = self._client.models.list(timeout=_remaining(deadline))
            except network.ProviderDeadlineError:
                _failed()
                raise
            except network.ProviderConnectivityError:
                _failed()
                raise
            except Exception as error:
                failure = _status_failure(error)
                if isinstance(failure, network.ProviderConnectivityError):
                    _failed()
                raise failure from None
            return sorted(m.id for m in response.data)

    def _call(self, tier: str, system: str, user: Sequence[Passage], budget: int, json_mode: bool,
              temperature: float, purpose: Purpose, deadline: float | None = None):
        prepared = prepare_outbound(purpose, user)
        deadline = deadline or (time.monotonic() + _deadline_seconds.get())
        self._check_credentials()
        from .transport import DirectTransport
        transport=getattr(self,'transport',None) or DirectTransport(self)
        return transport.infer(tier,system,user,budget,json_mode,temperature,purpose,deadline)

    def _retry_call(self, operation, deadline: float):
        """Retry only sanitized transport failures; SDK retries stay disabled."""
        if getattr(self,'transport',None) is not None: return operation()
        return provider_call(operation, deadline, service='nebius')

    def ask(
        self,
        *,
        system: str,
        user: Sequence[Passage],
        purpose: Purpose,
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
        deadline = time.monotonic() + _deadline_seconds.get()
        with _deadline_guard(deadline):
            if getattr(self,"transport",None) is None: _check_cooldown()
            budget = max_tokens + REASONING_HEADROOM
            msg = self._retry_call(
                lambda: self._call(tier, system, user, budget, json_mode,
                                   temperature, purpose, deadline), deadline)
            content = (msg.content or "").strip()
            if content:
                return content

            if getattr(msg, "reasoning", None):
                msg = self._retry_call(
                    lambda: self._call(tier, system, user, budget * 2, json_mode,
                                       temperature, purpose, deadline), deadline)
                content = (msg.content or "").strip()
            return content

    @staticmethod
    def _answer_only(raw: str) -> str:
        """The reply with the model's thinking taken out of it.

        Nemotron puts its reasoning in a separate `reasoning` field, so `ask`
        never has to do this. MiniCPM does not: measured live on 2026-09-05, it
        returns `<think>…</think>` inside `content`, ahead of the answer. Left
        alone that reasoning gets written into the user's own note. An
        unterminated block is the model having spent the whole budget thinking
        — nothing in it is an answer, so it is dropped and asked again bigger.
        """
        text = _THINK.sub("", raw)
        return _UNCLOSED_THINK.sub("", text).strip()

    def _look(self, model: str, question: str, data_uri: str, budget: int,
              temperature: float, source: Passage, deadline: float):
        from .policy import PolicyError
        from . import notes, policy
        if not isinstance(source, Passage) or source.origin != 'note' or not source.note_id:
            raise PolicyError('Vision requires note provenance.')
        question = prepare_outbound('write', [source, Passage(question, 'user_request')])[1]
        self._check_credentials()
        live = notes.get_note(source.note_id)
        if (live is None or live.id != source.note_id or not policy.require_ready().readable(live)
                or live.modified != source.modified):
            raise PolicyError('Vision source changed or is unavailable.')
        from .transport import DirectTransport
        transport=getattr(self,'transport',None)
        if transport is not None: return transport.vision(question,data_uri,budget,temperature,source,deadline)
        return DirectTransport(self).vision(question,data_uri,budget,temperature,source,deadline,model=model)

    def see(self, *, image: bytes, mime: str, question: str, source: Passage,
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
        from .policy import PolicyError
        if mime not in {'image/png', 'image/jpeg', 'image/gif', 'image/webp'} or not isinstance(image, bytes):
            raise PolicyError('Unsupported vision input.')

        model = os.environ.get("NOTRON_MODEL_VISION", VISION_MODEL)
        data_uri = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        budget = max_tokens + REASONING_HEADROOM

        deadline = time.monotonic() + _deadline_seconds.get()
        with _deadline_guard(deadline):
            if getattr(self,"transport",None) is None: _check_cooldown()
            msg = self._retry_call(lambda: self._look(model, question, data_uri, budget,
                                    temperature, source, deadline), deadline)
            raw = msg.content or ""
            content = self._answer_only(raw)
            if content:
                return content
            if getattr(msg, "reasoning", None) or "<think" in raw.lower():
                msg = self._retry_call(lambda: self._look(model, question, data_uri, budget * 2,
                                        temperature, source, deadline), deadline)
                content = self._answer_only(msg.content or "")
            return content

    def embed(self, passages: Sequence[Passage]) -> list[list[float]]:
        """Vectorise a batch of note chunks. Batches of ~64 keep requests small."""
        texts = prepare_outbound("embed", passages)
        import os as _os

        model = _os.environ.get("NOTRON_MODEL_EMBED", EMBED_MODEL)
        deadline = time.monotonic() + BATCH_DEADLINE
        with _deadline_guard(deadline):
            if getattr(self,"transport",None) is None: _check_cooldown()
            out: list[list[float]] = []
            for i in range(0, len(texts), 64):
                def request():
                    self._check_credentials()
                    from .transport import DirectTransport
                    transport=getattr(self,'transport',None) or DirectTransport(self)
                    return transport.embed(passages[i:i+64],deadline)
                out.extend(self._retry_call(request,deadline))
            return out

    def ask_json(self, *, system: str, user: Sequence[Passage], purpose: Purpose, tier: str = "fast", **kw) -> dict:
        """Parse a model's JSON, tolerating the three ways it usually goes wrong:
        prose wrapped around the object, a markdown fence, or a reply truncated
        mid-string by the token budget."""
        raw = self.ask(system=system, user=user, tier=tier, purpose=purpose, json_mode=True, **kw)
        for candidate in _json_candidates(raw):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        raise ValueError("model did not return usable JSON")


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
