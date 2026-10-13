"""Gemini provider: request shape, response parsing, errors, provider selection."""
import json

import httpx
import pytest

from app.ai.provider import GeminiProvider, LlmError, get_provider
from app.config import Config


def test_request_and_response_shape():
    seen = {}

    def handler(req):
        seen["path"], seen["key"], seen["body"] = req.url.path, req.headers.get("x-goog-api-key"), json.loads(req.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"thought": True, "text": "thinking"}, {"text": '{"ok": true}'}]}, "finishReason": "STOP"}],
                                         "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4}, "modelVersion": "gemini-x"})

    p = GeminiProvider(Config(gemini_api_key="K", gemini_model="gemini-2.5-pro"), transport=httpx.MockTransport(handler))
    r = p.complete("be exact", [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}, {"role": "user", "content": "again"}])
    assert r.text == '{"ok": true}' and r.provider == "gemini" and r.input_tokens == 11  # reasoning "thought" parts are not part of the answer
    assert seen["path"].endswith("/models/gemini-2.5-pro:generateContent") and seen["key"] == "K"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "be exact"
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model", "user"]
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_provider_selection():
    assert get_provider(Config(ai_provider="gemini", gemini_api_key="K")).name == "gemini"
    assert get_provider(Config(ai_provider="auto", gemini_api_key="K", anthropic_api_key="A")).name == "api"  # the Anthropic key wins in auto mode
    assert get_provider(Config(ai_provider="auto", gemini_api_key="K")).name == "gemini"
    assert get_provider(Config(ai_provider="gemini", gemini_api_key="")).available is False
    assert get_provider(Config(ai_provider="off", gemini_api_key="K")).available is False


def test_errors_are_explained():
    def quota(req):
        return httpx.Response(429, json={"error": {"message": "Quota exceeded"}})

    def blocked(req):
        return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})

    def empty(req):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]})

    for handler, pattern in ((quota, "429.*Quota"), (blocked, "SAFETY"), (empty, "MAX_TOKENS")):
        with pytest.raises(LlmError, match=pattern):
            GeminiProvider(Config(gemini_api_key="K"), transport=httpx.MockTransport(handler)).complete("s", [{"role": "user", "content": "x"}])


def test_overloaded_model_falls_back_to_the_next_one(monkeypatch):
    import app.ai.provider as pv
    monkeypatch.setattr(pv.time, "sleep", lambda s: None)
    tried = []

    def handler(req):
        model = req.url.path.split("/models/")[1].split(":")[0]
        tried.append(model)
        if model == "main":
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}], "modelVersion": model})

    p = GeminiProvider(Config(gemini_api_key="K", gemini_model="main", gemini_fallback_models="backup1,backup2"), transport=httpx.MockTransport(handler))
    r = p.complete("s", [{"role": "user", "content": "x"}])
    assert r.model == "backup1" and tried == ["main", "main", "backup1"]  # two tries on the main model, then the fallback; backup2 untouched


def test_all_models_overloaded_gives_a_clear_error(monkeypatch):
    import app.ai.provider as pv
    monkeypatch.setattr(pv.time, "sleep", lambda s: None)
    def always(req): return httpx.Response(503, json={"error": {"message": "high demand"}})
    with pytest.raises(LlmError, match="503.*high demand"):
        GeminiProvider(Config(gemini_api_key="K", gemini_model="a", gemini_fallback_models="b"), transport=httpx.MockTransport(always)).complete("s", [{"role": "user", "content": "x"}])


# ---------- Ollama (local model) and the backup chain ----------
def _ollama(handler):
    from app.ai.provider import OllamaProvider
    return OllamaProvider(Config(ollama_model="qwen2.5:7b"), transport=httpx.MockTransport(handler))


def test_ollama_request_and_response_shape():
    seen = {}

    def handler(req):
        seen["path"], seen["body"] = req.url.path, json.loads(req.content)
        return httpx.Response(200, json={"model": "qwen2.5:7b", "message": {"role": "assistant", "content": '{"ok": true}'}, "prompt_eval_count": 50, "eval_count": 7})

    r = _ollama(handler).complete("be exact", [{"role": "user", "content": "hi"}])
    assert r.text == '{"ok": true}' and r.provider == "ollama" and r.input_tokens == 50
    assert seen["path"] == "/api/chat" and seen["body"]["format"] == "json" and seen["body"]["stream"] is False
    assert seen["body"]["messages"][0] == {"role": "system", "content": "be exact"}


def test_ollama_errors_are_explained():
    with pytest.raises(LlmError, match="HTTP 500"):
        _ollama(lambda req: httpx.Response(500, text="boom")).complete("s", [{"role": "user", "content": "x"}])

    def down(req):
        raise httpx.ConnectError("refused")
    with pytest.raises(LlmError, match="could not reach Ollama"):
        _ollama(down).complete("s", [{"role": "user", "content": "x"}])


def test_main_failure_falls_back_to_the_local_model():
    from app.ai.provider import WithBackup

    class Dead(GeminiProvider):
        def __init__(self): self.model, self.name = "g", "gemini"
        def complete(self, *a, **k): raise LlmError("Gemini returned HTTP 429: quota")

    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b"}]})
        return httpx.Response(200, json={"model": "qwen2.5:7b", "message": {"content": "{}"}})

    r = WithBackup(Dead(), _ollama(handler)).complete("s", [{"role": "user", "content": "x"}])
    assert r.provider == "ollama"


def test_no_backup_available_keeps_the_original_error():
    from app.ai.provider import WithBackup

    class Dead(GeminiProvider):
        def __init__(self): self.model, self.name = "g", "gemini"
        def complete(self, *a, **k): raise LlmError("Gemini returned HTTP 429: quota")

    with pytest.raises(LlmError, match="429"):
        WithBackup(Dead(), _ollama(lambda req: httpx.Response(200, json={"models": []}))).complete("s", [{"role": "user", "content": "x"}])


def test_provider_selection_with_backup():
    p = get_provider(Config(ai_provider="gemini", gemini_api_key="K"))
    assert p.name == "gemini" and type(p).__name__ == "WithBackup"
    assert type(get_provider(Config(ai_provider="gemini", gemini_api_key="K", ollama_fallback=False))).__name__ == "GeminiProvider"
    assert get_provider(Config(ai_provider="ollama")).name == "ollama"


def test_quota_exhausted_model_moves_on_to_the_next_without_waiting(monkeypatch):
    import app.ai.provider as pv
    slept = []
    monkeypatch.setattr(pv.time, "sleep", lambda s: slept.append(s))
    tried = []

    def handler(req):
        model = req.url.path.split("/models/")[1].split(":")[0]
        tried.append(model)
        if model == "main":
            return httpx.Response(429, json={"error": {"message": "You exceeded your current quota"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}], "modelVersion": model})

    p = GeminiProvider(Config(gemini_api_key="K", gemini_model="main", gemini_fallback_models="backup1"), transport=httpx.MockTransport(handler))
    r = p.complete("s", [{"role": "user", "content": "x"}])
    assert r.model == "backup1" and tried == ["main", "backup1"] and not slept  # one try on the exhausted model, no sleeping
