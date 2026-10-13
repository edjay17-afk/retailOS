"""LLM providers. The AI layer talks to a model only through this small interface.

  api  : Anthropic Messages API (needs ANTHROPIC_API_KEY)
  cli  : the local Claude command line (`claude -p`), so no API key is needed if Claude is installed and signed in
  ollama: a model running locally through Ollama (nothing leaves this computer); also the backup when the main model fails
  off  : AI features are hidden and the deterministic screens carry on unchanged
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

import httpx

from ..config import Config, get_config

log = logging.getLogger("retailos.ai")


class LlmError(Exception):
    pass


@dataclass
class LlmResult:
    text: str
    provider: str
    model: str
    duration_ms: int
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class Provider:
    name = "off"
    model = ""
    available = False

    def complete(self, system: str, messages: list[dict], max_tokens: int = 3000, timeout: int = 150) -> LlmResult:  # pragma: no cover
        raise LlmError("AI is turned off")


class AnthropicApiProvider(Provider):
    name = "api"
    available = True

    def __init__(self, cfg: Config, transport: httpx.BaseTransport | None = None):
        self.model = cfg.ai_model
        self.http = httpx.Client(base_url="https://api.anthropic.com", timeout=150, transport=transport,
                                 headers={"x-api-key": cfg.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})

    def complete(self, system: str, messages: list[dict], max_tokens: int = 3000, timeout: int = 150) -> LlmResult:
        t = time.monotonic()
        try:
            r = self.http.post("/v1/messages", json={"model": self.model, "max_tokens": max_tokens, "system": system, "messages": messages}, timeout=timeout)
        except httpx.HTTPError as e:
            raise LlmError(f"could not reach the AI service: {type(e).__name__}") from e
        if r.status_code != 200:
            raise LlmError(f"AI service returned HTTP {r.status_code}: {r.text[:200]}")
        d = r.json()
        text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        u = d.get("usage", {})
        return LlmResult(text, self.name, d.get("model", self.model), int((time.monotonic() - t) * 1000), None, u.get("input_tokens"), u.get("output_tokens"))


class GeminiProvider(Provider):
    """Google Gemini through the Generative Language API (key from Google AI Studio)."""
    name = "gemini"
    available = True

    def __init__(self, cfg: Config, transport: httpx.BaseTransport | None = None):
        self.model = cfg.gemini_model
        self.fallbacks = [m.strip() for m in cfg.gemini_fallback_models.split(",") if m.strip()]
        self.http = httpx.Client(base_url="https://generativelanguage.googleapis.com", timeout=180, transport=transport,
                                 headers={"x-goog-api-key": cfg.gemini_api_key, "content-type": "application/json"})

    def complete(self, system: str, messages: list[dict], max_tokens: int = 3000, timeout: int = 180) -> LlmResult:
        t = time.monotonic()
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in messages],
                # thinking models spend part of the budget on reasoning, so leave generous room for the answer itself
                "generationConfig": {"maxOutputTokens": max(max_tokens, 1) * 3 + 4096, "temperature": 0.2, "responseMimeType": "application/json"}}
        r = None
        # Google answers 5xx "high demand" now and then: retry briefly, then fall back to the next model in the list
        for model in [self.model] + [m for m in self.fallbacks if m != self.model]:
            for attempt in range(1, 3):
                try:
                    r = self.http.post(f"/v1beta/models/{model}:generateContent", json=body, timeout=timeout)
                except httpx.HTTPError as e:
                    r = None
                    last_err = e
                    time.sleep(3 * attempt)
                    continue
                if r.status_code in (500, 502, 503, 504):
                    time.sleep(3 * attempt)
                    continue
                break  # 429 (quota) is not worth retrying on the same model: each model has its own quota, so move on to the next one
            if r is not None and r.status_code not in (429, 500, 502, 503, 504):
                break
        if r is None:
            raise LlmError(f"could not reach the AI service: {type(last_err).__name__}")
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", "")
            except ValueError:
                msg = r.text
            raise LlmError(f"Gemini returned HTTP {r.status_code}: {msg[:200]}")
        d = r.json()
        cands = d.get("candidates") or []
        if not cands:
            block = (d.get("promptFeedback") or {}).get("blockReason")
            raise LlmError("Gemini returned no answer" + (f" ({block})" if block else ""))
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(x.get("text", "") for x in parts if not x.get("thought"))
        if not text.strip():
            raise LlmError(f"Gemini returned an empty answer (finish reason {cands[0].get('finishReason')})")
        u = d.get("usageMetadata", {})
        return LlmResult(text, self.name, d.get("modelVersion", self.model), int((time.monotonic() - t) * 1000), None,
                         u.get("promptTokenCount"), u.get("candidatesTokenCount"))


class ClaudeCliProvider(Provider):
    name = "cli"
    available = True

    def __init__(self, path: str, model: str = ""):
        self.path = path
        self.cli_model = model
        self.model = model or "claude (command line)"

    def complete(self, system: str, messages: list[dict], max_tokens: int = 3000, timeout: int = 150) -> LlmResult:
        transcript = "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in messages)
        t = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as cwd:  # neutral folder: the model sees no project files
                p = subprocess.run([self.path, "-p", *(["--model", self.cli_model] if self.cli_model else []), "--output-format", "json", "--tools", "", "--no-session-persistence",
                                    "--setting-sources", "", "--system-prompt", system], input=transcript, capture_output=True, text=True, timeout=timeout, cwd=cwd, encoding="utf-8")
        except subprocess.TimeoutExpired as e:
            raise LlmError("the AI took too long to answer") from e
        except OSError as e:
            raise LlmError(f"could not start Claude: {e}") from e
        if p.returncode != 0:
            raise LlmError(f"Claude exited with code {p.returncode}: {(p.stderr or p.stdout)[:200]}")
        try:
            d = json.loads(p.stdout)
        except ValueError as e:
            raise LlmError("Claude returned something unreadable") from e
        if d.get("is_error"):
            raise LlmError(f"Claude reported an error: {str(d.get('result'))[:200]}")
        used = next(iter((d.get("modelUsage") or {"": {}}).keys()), self.model)
        return LlmResult(d.get("result", ""), self.name, used or self.model, int((time.monotonic() - t) * 1000), d.get("total_cost_usd"))


class OllamaProvider(Provider):
    """A local model served by Ollama. Slower and less careful than the cloud models, but private, free and offline."""
    name = "ollama"
    available = True

    def __init__(self, cfg: Config, transport: httpx.BaseTransport | None = None):
        self.model = cfg.ollama_model
        self.http = httpx.Client(base_url=cfg.ollama_url.rstrip("/"), timeout=300, transport=transport)

    def reachable(self) -> bool:
        try:
            r = self.http.get("/api/tags", timeout=2)
            return r.status_code == 200 and any((m.get("name") or "").split(":")[0] == self.model.split(":")[0] and (m.get("name") == self.model or ":" not in self.model)
                                                for m in r.json().get("models", []))
        except (httpx.HTTPError, ValueError):
            return False

    def complete(self, system: str, messages: list[dict], max_tokens: int = 3000, timeout: int = 300) -> LlmResult:
        t = time.monotonic()
        body = {"model": self.model, "stream": False, "format": "json", "keep_alive": "10m",
                "messages": [{"role": "system", "content": system}] + [{"role": m["role"], "content": m["content"]} for m in messages],
                "options": {"temperature": 0.2, "num_ctx": 16384, "num_predict": max(max_tokens, 1) * 2 + 1024}}
        try:
            r = self.http.post("/api/chat", json=body, timeout=timeout)
        except httpx.HTTPError as e:
            raise LlmError(f"could not reach Ollama at {self.http.base_url}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise LlmError(f"Ollama returned HTTP {r.status_code}: {r.text[:200]}")
        d = r.json()
        text = (d.get("message") or {}).get("content", "")
        if not text.strip():
            raise LlmError("Ollama returned an empty answer")
        return LlmResult(text, self.name, d.get("model", self.model), int((time.monotonic() - t) * 1000), None, d.get("prompt_eval_count"), d.get("eval_count"))


class WithBackup(Provider):
    """Uses the main provider; if it fails (down, over quota, overloaded) the local backup answers instead."""
    available = True

    def __init__(self, main: Provider, backup: OllamaProvider):
        self.main, self.backup = main, backup
        self.name, self.model = main.name, main.model

    def complete(self, system: str, messages: list[dict], max_tokens: int = 3000, timeout: int = 150) -> LlmResult:
        try:
            return self.main.complete(system, messages, max_tokens, timeout)
        except LlmError as e:
            if not self.backup.reachable():
                raise
            log.warning("main AI provider failed (%s); using local %s", e, self.backup.model)
            return self.backup.complete(system, messages, max_tokens)


def get_provider(cfg: Config | None = None) -> Provider:
    cfg = cfg or get_config()
    mode = (cfg.ai_provider or "auto").lower()
    if mode == "off":
        return Provider()
    main = _main_provider(cfg, mode)
    if main is not None:
        if cfg.ollama_fallback and mode != "ollama" and main.name != "ollama":
            return WithBackup(main, OllamaProvider(cfg))
        return main
    return Provider()


def _main_provider(cfg: Config, mode: str) -> Provider | None:
    if mode in ("auto", "api") and cfg.anthropic_api_key:
        return AnthropicApiProvider(cfg)
    if mode in ("auto", "gemini") and cfg.gemini_api_key:
        return GeminiProvider(cfg)
    if mode in ("auto", "cli"):
        path = cfg.ai_cli_path or shutil.which("claude")
        if path:
            return ClaudeCliProvider(path, cfg.ai_cli_model)
    if mode == "ollama" or mode == "auto":
        o = OllamaProvider(cfg)
        if mode == "ollama" or o.reachable():
            return o
    return None
