"""Model tiers — T1 local, T2 cloud, and honest absence.

The local model's job is narrow and fixed: **single-turn intent classification
and slot filling**. It never composes multi-step tool sequences. That restraint
is not caution, it is what makes a 4B model viable — BFCL v4 weights 70% toward
agentic and multi-turn work, which is exactly the part playbooks remove
(docs/11 Finding 4, docs/15 §2.1).

Absence is a first-class state. With no model reachable, every playbook still
runs at full reliability, because the deterministic path never needed one. The
system degrades to "no fuzzy routing", not to "broken" (docs/15 §4).

No new dependencies: Ollama speaks HTTP and the standard library can too.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

# 127.0.0.1, not "localhost": on Windows the name resolves to ::1 first, and
# the IPv6 attempt has to fail before IPv4 is tried — doubling every probe.
DEFAULT_HOST = os.environ.get("TANGO_OLLAMA", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("TANGO_MODEL", "qwen3:4b")

# Small model, narrow job, short context. 8K not 32K: the 32K figure is
# benchmark-shaped, and it is the line item that breaks an 8 GB VRAM budget.
DEFAULT_OPTIONS: dict[str, Any] = {
    "temperature": 0.0,
    "num_ctx": 8192,
    "num_predict": 256,
}


class ModelUnavailable(RuntimeError):
    """No model could be reached. Callers must degrade, never pretend."""


@dataclass(frozen=True)
class Completion:
    text: str
    parsed: dict[str, Any] | None
    model: str
    ms: int
    tier: str


class Model(Protocol):
    name: str
    tier: str

    def available(self) -> bool: ...

    def complete(
        self, prompt: str, *, system: str = "", schema: dict[str, Any] | None = None
    ) -> Completion: ...


@dataclass
class OllamaModel:
    """T1. Structured output via Ollama's ``format`` parameter, which constrains
    decoding rather than asking politely — schema validity becomes a property of
    generation, not a retry loop (docs/14 §1)."""

    name: str = DEFAULT_MODEL
    host: str = DEFAULT_HOST
    tier: str = "T1"
    options: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_OPTIONS))
    timeout_s: float = 30.0
    _available: bool | None = field(default=None, repr=False)

    def available(self) -> bool:
        """Is the model actually there? Cached per instance.

        The probe is cheap when Ollama is running and slow when it is not,
        which is exactly the wrong way round — so it happens once, lazily,
        and only when a rule has already missed.
        """
        if self._available is None:
            self._available = self._probe()
        return self._available

    def _probe(self) -> bool:
        try:
            with urllib.request.urlopen(  # noqa: S310
                f"{self.host}/api/tags", timeout=1.5
            ) as r:
                tags = json.loads(r.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            return False
        stem = self.name.split(":")[0]
        return any(m.get("name", "").split(":")[0] == stem for m in tags.get("models", []))

    def complete(
        self, prompt: str, *, system: str = "", schema: dict[str, Any] | None = None
    ) -> Completion:
        import time

        payload: dict[str, Any] = {
            "model": self.name,
            "prompt": prompt,
            "stream": False,
            "options": self.options,
        }
        if system:
            payload["system"] = system
        if schema is not None:
            payload["format"] = schema

        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.host}/api/generate", data=body, headers={"Content-Type": "application/json"}
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310
                data = json.loads(r.read().decode())
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ModelUnavailable(f"{self.name} at {self.host}: {exc}") from exc

        text = str(data.get("response", "")).strip()
        parsed: dict[str, Any] | None = None
        if schema is not None:
            try:
                candidate = json.loads(text)
                parsed = candidate if isinstance(candidate, dict) else None
            except json.JSONDecodeError:
                parsed = None
        return Completion(
            text=text,
            parsed=parsed,
            model=self.name,
            ms=int((time.monotonic() - started) * 1000),
            tier=self.tier,
        )


@dataclass
class StubModel:
    """A deterministic stand-in for machines with no model runtime.

    This is the "fake brain" the whole Phase 0 sequence is built on: prove the
    spine against something with zero variance, so that when a real model
    arrives the only new suspect is the model (docs/16 §14.2). It is honest
    about what it is — it never guesses an intent it was not told about.
    """

    name: str = "stub"
    tier: str = "T1-stub"
    responses: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Keyed by *utterance*, not by the whole prompt. Keying on the prompt makes
    every test hostage to prompt formatting, which is the wrong thing to pin."""

    def available(self) -> bool:
        return True

    @staticmethod
    def _utterance(prompt: str) -> str:
        for line in prompt.splitlines():
            if line.lower().startswith("utterance:"):
                return line.split(":", 1)[1].strip().lower()
        return prompt.strip().lower()

    def complete(
        self, prompt: str, *, system: str = "", schema: dict[str, Any] | None = None
    ) -> Completion:
        parsed = self.responses.get(self._utterance(prompt))
        return Completion(
            text=json.dumps(parsed) if parsed else "",
            parsed=parsed,
            model=self.name,
            ms=0,
            tier=self.tier,
        )


@dataclass
class NullModel:
    """No model at all. Every call raises, so callers are forced to handle
    absence explicitly rather than silently producing a worse answer."""

    name: str = "none"
    tier: str = "none"

    def available(self) -> bool:
        return False

    def complete(
        self, prompt: str, *, system: str = "", schema: dict[str, Any] | None = None
    ) -> Completion:
        raise ModelUnavailable("no local model is configured or reachable")


# --------------------------------------------------------------------- routing

ROUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "playbook": {"type": "string"},
        "project": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["playbook", "confidence"],
}

ROUTE_SYSTEM = """You map a single user utterance to one playbook.

Rules:
- Choose only from the playbook list given. Never invent a name.
- For 'project', copy a phrase from the utterance. Do not guess a project that
  was not mentioned; leave it empty instead.
- If nothing fits, answer with playbook "none".
- Confidence is 0.0-1.0. Be honest; a low number is more useful than a wrong
  high one.
Answer only with JSON."""


def build_route_prompt(utterance: str, playbooks: list[str], projects: list[str]) -> str:
    return (
        f"Playbooks: {', '.join(sorted(playbooks))}\n"
        f"Known projects: {', '.join(sorted(projects)) or '(none)'}\n"
        f"Utterance: {utterance}\n"
    )


class LazyLocalModel:
    """A T1 that does not exist until something needs it.

    An audit found every CLI command paying ~4s to probe for Ollama — including
    commands that never use a model. That is the architecture's own principle
    violated at startup: deterministic first, model only as fallback. Nothing
    here touches the network until the router has already exhausted its rules.
    """

    tier = "T1"

    def __init__(self, name: str = DEFAULT_MODEL, host: str = DEFAULT_HOST) -> None:
        self.name = name
        self.host = host
        self._inner: OllamaModel | None = None

    def _resolve(self) -> OllamaModel:
        if self._inner is None:
            self._inner = OllamaModel(name=self.name, host=self.host)
        return self._inner

    def available(self) -> bool:
        return self._resolve().available()

    def complete(
        self, prompt: str, *, system: str = "", schema: dict[str, Any] | None = None
    ) -> Completion:
        model = self._resolve()
        if not model.available():
            raise ModelUnavailable(f"{self.name} is not reachable at {self.host}")
        return model.complete(prompt, system=system, schema=schema)


def select_model(prefer_local: bool = True) -> Model:
    """Return the T1 handle. Deliberately does **not** probe.

    Probing here would make every command pay for a model most of them never
    use. The router asks ``available()`` only when its rules have missed.
    """
    return LazyLocalModel() if prefer_local else NullModel()
