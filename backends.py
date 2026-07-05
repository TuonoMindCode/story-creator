"""LLM backend layer.

All three backends (KoboldCpp, llama.cpp server, Ollama) are reached over HTTP.
Generation uses the OpenAI-compatible /v1/chat/completions endpoint with SSE
streaming ("auto" template mode, the server applies its chat template), or the
raw /v1/completions endpoint with a client-side chat template ("manual" mode).
"""
from __future__ import annotations

import json
import random
import threading
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterator, Optional

import requests

BACKEND_KOBOLDCPP = "koboldcpp"
BACKEND_LLAMACPP = "llama.cpp"
BACKEND_OLLAMA = "ollama"

BACKENDS = [BACKEND_KOBOLDCPP, BACKEND_LLAMACPP, BACKEND_OLLAMA]

# pipeline sections, each with its own LLM configuration
SECTION_KEYS = ["storyboard", "planner", "writer", "summarizer"]
SECTION_LABELS = {
    "storyboard": "Storyboard",
    "planner": "Scene Planner",
    "writer": "Scene Writer",
    "summarizer": "Summarizer",
}
# typical safe defaults per section; the writer gets a mild temperature RANGE
# for variety between stories without making the model go off the rails
SECTION_DEFAULTS = {
    "storyboard": {"temperature": [0.9, 0.9], "max_tokens": 2048},
    "planner": {"temperature": [0.7, 0.7], "max_tokens": 1536},
    # generous reply space so a scene (or a thinking model) never gets cut off;
    # mild smoothing (KoboldCpp only) — the community-standard fiction recipe
    "writer": {"temperature": [0.7, 1.0], "smoothing_factor": [0.15, 0.3],
               "max_tokens": 4096},
    # low temperature so scene summaries stay factual
    "summarizer": {"temperature": [0.3, 0.3], "max_tokens": 512},
}


def default_section_params(key: str):
    """Fresh GenParams with the typical defaults for a pipeline section."""
    p = GenParams()
    for pk, pv in SECTION_DEFAULTS.get(key, {}).items():
        if pk == "max_tokens":
            p.max_tokens = pv
        else:
            p.ranges[pk] = list(pv)
    return p

DEFAULT_URLS = {
    BACKEND_KOBOLDCPP: "http://localhost:5001",
    BACKEND_LLAMACPP: "http://localhost:8080",
    BACKEND_OLLAMA: "http://localhost:11434",
}

# ---------------------------------------------------------------------------
# Chat templates for "manual" mode (raw completion endpoint).
# {system} / {user} are filled in; the result is sent as a single prompt.
# ---------------------------------------------------------------------------
CHAT_TEMPLATES = {
    "ChatML": (
        "<|im_start|>system\n{system}<|im_end|>\n"
        "<|im_start|>user\n{user}<|im_end|>\n"
        "<|im_start|>assistant\n"
    ),
    "Llama-3": (
        "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{system}<|eot_id|>"
        "<|start_header_id|>user<|end_header_id|>\n\n{user}<|eot_id|>"
        "<|start_header_id|>assistant<|end_header_id|>\n\n"
    ),
    "Mistral": ("[INST] {system}\n\n{user} [/INST]"),
    "Alpaca": (
        "{system}\n\n### Instruction:\n{user}\n\n### Response:\n"
    ),
    "Gemma": (
        "<start_of_turn>user\n{system}\n\n{user}<end_of_turn>\n"
        "<start_of_turn>model\n"
    ),
}

TEMPLATE_STOP_STRINGS = {
    "ChatML": ["<|im_end|>", "<|im_start|>"],
    "Llama-3": ["<|eot_id|>"],
    "Mistral": ["[INST]", "</s>"],
    "Alpaca": ["### Instruction:"],
    "Gemma": ["<end_of_turn>", "<start_of_turn>"],
}

TEMPLATE_MODE_AUTO = "auto"  # chat endpoint, server-side template
TEMPLATE_MODE_MANUAL = "manual"  # completion endpoint, client-side template


# parameters expressed as [min, max] ranges — a value is rolled once per story
RANGE_PARAMS = [
    # (name, label, lo-limit, hi-limit, step, decimals, default, kobold_only)
    ("temperature", "Temperature", 0.0, 3.0, 0.05, 2, 0.8, False),
    ("top_p", "Top P", 0.0, 1.0, 0.01, 2, 0.95, False),
    ("top_k", "Top K", 0, 500, 1, 0, 40, False),
    ("min_p", "Min P", 0.0, 1.0, 0.01, 2, 0.05, False),
    ("repeat_penalty", "Repeat penalty", 0.5, 2.0, 0.01, 2, 1.1, False),
    ("smoothing_factor", "Smoothing factor", 0.0, 10.0, 0.05, 2, 0.0, True),
    ("tfs", "TFS", 0.0, 1.0, 0.01, 2, 1.0, True),
    ("typical", "Typical P", 0.0, 1.0, 0.01, 2, 1.0, True),
    ("top_a", "Top A", 0.0, 1.0, 0.01, 2, 0.0, True),
]
# kobold "off" values — these are not sent when left at their default
_KOBOLD_OFF = {"smoothing_factor": 0.0, "tfs": 1.0, "typical": 1.0, "top_a": 0.0}


@dataclass
class GenParams:
    """Sampling parameters; every ranged parameter is a [min, max] pair."""
    ranges: dict = field(default_factory=lambda: {
        r[0]: [r[6], r[6]] for r in RANGE_PARAMS})
    max_tokens: int = 2048
    seed: int = -1  # -1 = random

    def get_range(self, name: str) -> list:
        default = next(r[6] for r in RANGE_PARAMS if r[0] == name)
        rng = self.ranges.setdefault(name, [default, default])
        return rng

    def rolled(self) -> "GenParams":
        """A copy where every [min, max] range is collapsed to one rolled value."""
        p = GenParams(max_tokens=self.max_tokens, seed=self.seed)
        p.ranges = {}
        for name, pair in self.ranges.items():
            lo, hi = float(pair[0]), float(pair[1])
            v = lo if hi <= lo else random.uniform(lo, hi)
            is_int = any(r[0] == name and r[5] == 0 for r in RANGE_PARAMS)
            v = int(round(v)) if is_int else round(v, 3)
            p.ranges[name] = [v, v]
        return p

    def value(self, name: str) -> float:
        """Concrete value for a rolled config (min of the range otherwise)."""
        return self.get_range(name)[0]

    def describe(self) -> str:
        parts = [f"{name}={self.value(name)}" for name in self.ranges]
        parts.append(f"max_tokens={self.max_tokens}")
        parts.append(f"seed={'random' if self.seed < 0 else self.seed}")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GenParams":
        p = cls()
        d = d or {}
        if "ranges" in d:
            for name, pair in (d.get("ranges") or {}).items():
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    p.ranges[name] = [pair[0], pair[1]]
        else:
            # migrate old flat format (single values per parameter)
            for name in list(p.ranges):
                if name in d and isinstance(d[name], (int, float)):
                    p.ranges[name] = [d[name], d[name]]
        if isinstance(d.get("max_tokens"), (int, float)):
            p.max_tokens = int(d["max_tokens"])
        if isinstance(d.get("seed"), (int, float)):
            p.seed = int(d["seed"])
        return p


@dataclass
class BackendSettings:
    """Per-server settings (one per backend type, shared by all sections)."""
    base_url: str = ""
    template_mode: str = TEMPLATE_MODE_AUTO
    template_name: str = "ChatML"
    custom_template: str = ""
    context_length: int = 8192
    timeout: int = 0  # seconds; 0 = infinite (no read timeout)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BackendSettings":
        b = cls()
        for k, v in (d or {}).items():
            if hasattr(b, k):
                setattr(b, k, v)
        return b


def default_backend_settings() -> dict[str, BackendSettings]:
    return {name: BackendSettings(base_url=DEFAULT_URLS[name]) for name in BACKENDS}


@dataclass
class SectionConfig:
    """Full LLM configuration for one pipeline section.

    The per-section choice is backend/model/params/preset; the server plumbing
    fields (base_url, template, context, timeout) are filled in at runtime from
    the chosen backend's BackendSettings.
    """
    backend: str = BACKEND_KOBOLDCPP
    base_url: str = DEFAULT_URLS[BACKEND_KOBOLDCPP]
    model: str = ""
    params: GenParams = field(default_factory=GenParams)
    template_mode: str = TEMPLATE_MODE_AUTO
    template_name: str = "ChatML"
    custom_template: str = ""
    context_length: int = 8192
    timeout: int = 600
    prompt_preset: str = "Default"

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SectionConfig":
        c = cls()
        for k, v in (d or {}).items():
            if k == "params":
                c.params = GenParams.from_dict(v)
            elif hasattr(c, k):
                setattr(c, k, v)
        return c

    def with_backend_settings(self, settings: BackendSettings) -> "SectionConfig":
        """A copy of this config with the server plumbing filled in."""
        cfg = SectionConfig.from_dict(self.to_dict())
        cfg.base_url = settings.base_url or DEFAULT_URLS.get(cfg.backend, "")
        cfg.template_mode = settings.template_mode
        cfg.template_name = settings.template_name
        cfg.custom_template = settings.custom_template
        cfg.context_length = settings.context_length
        cfg.timeout = settings.timeout
        return cfg

    def rolled(self) -> "SectionConfig":
        """A copy with every min/max parameter range rolled to a fixed value."""
        cfg = SectionConfig.from_dict(self.to_dict())
        cfg.params = cfg.params.rolled()
        return cfg


# finish_reason of the most recent stream_generate call ("stop", "length", …);
# "length" means the output was cut off by Max tokens
LAST_FINISH_REASON: Optional[str] = None


class BackendError(Exception):
    pass


class ReasoningOnlyError(BackendError):
    """The model spent its whole token budget on hidden reasoning."""

    def __init__(self, msg: str, reasoning_tokens: int = 0):
        super().__init__(msg)
        self.reasoning_tokens = reasoning_tokens


def _v1(base_url: str) -> str:
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def list_models(cfg: SectionConfig) -> list[str]:
    """Return the model names the server offers (may be empty for koboldcpp)."""
    try:
        if cfg.backend == BACKEND_OLLAMA:
            r = requests.get(cfg.base_url.rstrip("/") + "/api/tags", timeout=10)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        r = requests.get(_v1(cfg.base_url) + "/models", timeout=10)
        r.raise_for_status()
        data = r.json().get("data", [])
        return [m.get("id", "") for m in data if m.get("id")]
    except requests.ConnectionError as e:
        raise BackendError(
            f"Cannot reach {cfg.backend} at {cfg.base_url} — is the server "
            "running?") from e
    except requests.RequestException as e:
        raise BackendError(f"Could not list models: {e}") from e


def test_connection(cfg: SectionConfig) -> str:
    """Returns a human-readable status line, raises BackendError on failure."""
    models = list_models(cfg)
    if models:
        return f"OK — {cfg.backend} at {cfg.base_url}, models: {', '.join(models[:8])}"
    return f"OK — {cfg.backend} at {cfg.base_url} (no model list; server decides)"


def _build_payload(cfg: SectionConfig, system: str, user: str) -> tuple[str, dict]:
    """Returns (endpoint_url, json_payload) for the configured mode."""
    if cfg.backend == BACKEND_OLLAMA and not cfg.model:
        raise BackendError(
            "Ollama requires a model name. On the Story Start tab, press the ↻ "
            "button next to the Model box for this section and pick a model."
        )
    p = cfg.params
    common = {
        "temperature": p.value("temperature"),
        "top_p": p.value("top_p"),
        "max_tokens": p.max_tokens,
        "stream": True,
        # Extra sampler fields: llama.cpp and koboldcpp accept these on their
        # OpenAI-compatible endpoints; ollama's /v1 ignores unknown fields.
        "top_k": int(p.value("top_k")),
        "min_p": p.value("min_p"),
        "repeat_penalty": p.value("repeat_penalty"),
    }
    if cfg.backend == BACKEND_KOBOLDCPP:
        common["rep_pen"] = p.value("repeat_penalty")  # kobold's native name
        for name, off in _KOBOLD_OFF.items():
            v = p.value(name)
            if abs(v - off) > 1e-9:
                common[name] = v
    if p.seed >= 0:
        common["seed"] = p.seed
    if cfg.model:
        common["model"] = cfg.model

    if cfg.template_mode == TEMPLATE_MODE_MANUAL:
        template = cfg.custom_template or CHAT_TEMPLATES.get(cfg.template_name, CHAT_TEMPLATES["ChatML"])
        prompt = template.replace("{system}", system).replace("{user}", user)
        payload = dict(common)
        payload["prompt"] = prompt
        stops = TEMPLATE_STOP_STRINGS.get(cfg.template_name)
        if stops and not cfg.custom_template:
            payload["stop"] = stops
        return _v1(cfg.base_url) + "/completions", payload

    payload = dict(common)
    payload["messages"] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return _v1(cfg.base_url) + "/chat/completions", payload


_THINK_BLOCK_RE = None  # compiled lazily (keep module import cheap)


def strip_think(text: str) -> str:
    """Remove <think>…</think> reasoning blocks that some models (Qwen3 etc.)
    put in front of their answer; an unclosed <think> drops the rest."""
    import re
    global _THINK_BLOCK_RE
    if _THINK_BLOCK_RE is None:
        _THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
    text = _THINK_BLOCK_RE.sub("", text)
    if "<think>" in text:  # unclosed block — everything after it is reasoning
        text = text.split("<think>", 1)[0]
    return text.strip()


def stream_generate(
    cfg: SectionConfig,
    system: str,
    user: str,
    cancel: Optional[threading.Event] = None,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> str:
    """Stream a completion; returns the final text (reasoning removed).

    Thinking models (Qwen3/QwQ/DeepSeek-R1 …) emit hidden reasoning first —
    either as a separate `reasoning`/`reasoning_content` delta field (Ollama,
    llama.cpp) or inline <think> tags (koboldcpp). Reasoning is streamed to
    on_chunk so the user sees activity, but it is NOT part of the returned
    text; the UI replaces the live view with the clean text when done.
    """
    global LAST_FINISH_REASON
    LAST_FINISH_REASON = None
    url, payload = _build_payload(cfg, system, user)
    full: list[str] = []
    reasoning_chars = 0
    # timeout <= 0 means "no read timeout" (generation may take arbitrarily long)
    read_timeout = cfg.timeout if cfg.timeout and cfg.timeout > 0 else None
    try:
        with requests.post(url, json=payload, stream=True,
                           timeout=(10, read_timeout)) as r:
            if r.status_code != 200:
                body = r.text[:500]
                raise BackendError(f"HTTP {r.status_code} from {url}: {body}")
            # SSE bodies are UTF-8, but servers often omit the charset header and
            # requests would then decode as Latin-1, garbling å/ä/ö and dashes
            r.encoding = "utf-8"
            for raw_line in r.iter_lines(decode_unicode=True):
                if cancel is not None and cancel.is_set():
                    break
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or [{}]
                if choices[0].get("finish_reason"):
                    LAST_FINISH_REASON = choices[0]["finish_reason"]
                piece, reasoning = _extract_piece(obj)
                if reasoning:
                    reasoning_chars += len(reasoning)
                    if on_chunk is not None:
                        on_chunk(reasoning)  # visible live, dropped from result
                if piece:
                    full.append(piece)
                    if on_chunk is not None:
                        on_chunk(piece)
    except requests.ConnectionError as e:
        raise BackendError(
            f"Cannot reach {cfg.backend} at {cfg.base_url} — is the server "
            "running?") from e
    except requests.RequestException as e:
        raise BackendError(f"Connection error talking to {url}: {e}") from e

    raw = "".join(full)
    text = strip_think(raw)
    reasoning_chars += len(raw) - len(text)
    if not text.strip() and reasoning_chars > 0 and not (cancel and cancel.is_set()):
        reasoning_tokens = int(reasoning_chars / 3.5)
        raise ReasoningOnlyError(
            f"The model produced ≈{reasoning_tokens} tokens of hidden "
            "reasoning ('thinking') but no actual text. Raise Max tokens for this "
            "section, or use a non-thinking model/variant (e.g. a Qwen "
            "'instruct' build instead of the thinking build).",
            reasoning_tokens=reasoning_tokens)
    return text


def _extract_piece(obj: dict) -> tuple[str, str]:
    """Returns (content, reasoning) from one streamed chunk."""
    choices = obj.get("choices")
    if not choices:
        return "", ""
    ch = choices[0]
    # chat endpoint
    delta = ch.get("delta")
    if delta is not None:
        reasoning = (delta.get("reasoning_content") or delta.get("reasoning")
                     or delta.get("thinking") or "")
        return delta.get("content") or "", reasoning
    # completion endpoint
    return ch.get("text") or "", ""
