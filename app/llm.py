import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from . import config, localization

log = logging.getLogger("llm")

ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "grok": "https://api.x.ai/v1",
    "kimi": "https://api.moonshot.ai/v1",
    "kimi_cn": "https://api.moonshot.cn/v1",
    "deepseek": "https://api.deepseek.com",
    "openrouter": "https://openrouter.ai/api/v1",
    "opencode_chat": "https://opencode.ai/zen/v1",
    "opencode_responses": "https://opencode.ai/zen/v1",
    "opencode_messages": "https://opencode.ai/zen/v1",
}


class LLMUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Connection:
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)

    def public(self):
        return {"provider": self.provider, "model": self.model,
                "base_url": self.base_url if self.provider == "compatible" else "",
                "has_key": bool(self.api_key), "configured": True}


def _connection(data, current=None):
    provider = data.get("provider")
    model = data.get("model", "").strip()
    base_url = data.get("base_url", "").strip().rstrip("/")
    key = data.get("api_key", "").strip()
    if provider not in (*ENDPOINTS, "compatible") or not model or len(model) > 200:
        raise ValueError("invalid_model")
    if len(key) > 4096 or any(c.isspace() or not c.isascii() for c in key):
        raise ValueError("invalid_key")
    if provider == "compatible":
        try:
            url = urlsplit(base_url)
            if not (len(base_url) <= 2048 and url.hostname and not (url.username or url.password or url.query or url.fragment)
                    and not any(c.isspace() or c == "\\" for c in base_url)
                    and (url.scheme == "https" or
                         (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}))):
                raise ValueError
            _ = url.port
        except ValueError:
            raise ValueError("invalid_url") from None
    else:
        base_url = ENDPOINTS[provider]
    if not key and current and (provider, base_url) == (current.provider, current.base_url):
        key = current.api_key
    if not key and (provider != "compatible" or urlsplit(base_url).hostname not in {"127.0.0.1", "localhost", "::1"}):
        raise ValueError("missing_key")
    return Connection(provider, model, base_url, key)


class LLM:
    def __init__(self):
        self._lock = threading.Lock()
        self._tls = threading.local()
        self._client = httpx.Client(follow_redirects=False, trust_env=False)
        self._path = config.DATA_DIR / "ai-connection.json"
        self.connection = None
        self._valid = False
        try:
            self.connection = _connection(json.loads(self._path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
        except (ValueError, OSError, AttributeError, TypeError):
            log.warning("Unable to load AI connection settings")

    @property
    def available(self):
        return self.connection is not None and self._valid

    @property
    def label(self):
        connection = self.connection
        return f"{connection.provider} / {connection.model}" if connection else "none"

    def settings(self):
        connection = self.connection
        return {**connection.public(), "available": self.available} if connection else {"configured": False, "has_key": False, "available": False}

    def save(self, data):
        with self._lock:
            connection = _connection(data, self.connection)
            self._test_connection(connection)
            config.ensure_dirs()
            with tempfile.NamedTemporaryFile(mode="w", dir=self._path.parent, encoding="utf-8", delete=False) as handle:
                temp_path = self._path.parent / os.path.basename(handle.name)
                try:
                    data = {**connection.public(), "api_key": connection.api_key}
                    json.dump(data, handle)
                    handle.close()
                    temp_path.replace(self._path)
                finally:
                    temp_path.unlink(missing_ok=True)
            self.connection = connection
            self._valid = True
            return self.settings()

    def clear(self):
        with self._lock:
            self._path.unlink(missing_ok=True)
            self.connection = None
            self._valid = False

    def test(self, data):
        current = self.connection
        connection = _connection(data, current)
        try:
            self._test_connection(connection)
        except LLMUnavailable:
            if current is not None and connection == current:
                with self._lock:
                    if self.connection is current:
                        self._valid = False
                from .pipeline import pipeline
                pipeline._pub_state()
            raise
        if current is not None and connection == current:
            with self._lock:
                if self.connection is current:
                    self._valid = True
            from .pipeline import pipeline
            pipeline._pub_state()

    def _test_connection(self, connection):
        text, _usage = self._request(connection, "Reply with OK.", "Connection test.", 512, 15, False)
        if not text:
            raise LLMUnavailable("empty_response")

    def verify_saved(self):
        connection = self.connection
        if connection is None:
            return
        try:
            self._test_connection(connection)
            valid = True
        except LLMUnavailable:
            valid = False
        with self._lock:
            if self.connection is not connection:
                return
            self._valid = valid
        from .pipeline import pipeline
        pipeline._pub_state()

    def _request(self, connection, system, user, max_tokens, timeout, json_mode):
        headers = {}
        messages = [{"role": "user", "content": user}]
        body = {"model": connection.model, "messages": messages}
        if connection.provider in ("anthropic", "opencode_messages"):
            headers = {"x-api-key": connection.api_key, "anthropic-version": "2023-06-01"}
            body.update(system=system, max_tokens=max_tokens)
            path = "/messages"
        elif connection.provider == "opencode_responses":
            headers = {"Authorization": f"Bearer {connection.api_key}"}
            body = {"model": connection.model, "instructions": system, "input": user,
                    "max_output_tokens": max_tokens}
            path = "/responses"
        else:
            if connection.api_key:
                headers["Authorization"] = f"Bearer {connection.api_key}"
            messages.insert(0, {"role": "system", "content": system})
            token_field = "max_completion_tokens" if connection.provider == "openai" else "max_tokens"
            body[token_field] = max_tokens
            if json_mode and connection.provider != "compatible":
                body["response_format"] = {"type": "json_object"}
            path = "/chat/completions"
        try:
            response = self._client.post(connection.base_url + path, headers=headers, json=body, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            usage = payload.get("usage") or {}
            if connection.provider in ("anthropic", "opencode_messages"):
                text = "".join(block["text"] for block in payload["content"] if block["type"] == "text")
                tokens = (usage.get("input_tokens"), usage.get("output_tokens"), usage.get("cache_read_input_tokens"))
            elif connection.provider == "opencode_responses":
                text = "".join(part.get("text", "") for item in payload.get("output", [])
                               for part in item.get("content", []) if part.get("type") == "output_text")
                tokens = (usage.get("input_tokens"), usage.get("output_tokens"),
                          (usage.get("input_tokens_details") or {}).get("cached_tokens"))
            else:
                text = payload["choices"][0]["message"].get("content") or ""
                tokens = (usage.get("prompt_tokens"), usage.get("completion_tokens"),
                          (usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
            return text.strip(), tuple(int(n or 0) for n in tokens)
        except httpx.HTTPStatusError as error:
            raise LLMUnavailable(f"http_{error.response.status_code}") from None
        except httpx.TimeoutException:
            raise LLMUnavailable("timeout") from None
        except (httpx.RequestError, httpx.InvalidURL):
            raise LLMUnavailable("connection_error") from None
        except (KeyError, IndexError, TypeError, AttributeError, ValueError):
            raise LLMUnavailable("invalid_response") from None

    def chat(self, system, user, max_tokens=1200, timeout=None, role="answer", json_mode=False):
        connection = self.connection
        if not self.available or connection is None:
            raise LLMUnavailable("missing_connection")
        start = time.monotonic()
        try:
            text, tokens = self._request(connection, localization.prompt(system), user, max_tokens, timeout or 30, json_mode)
        except LLMUnavailable as error:
            if str(error) in {"http_401", "http_403", "http_404"}:
                with self._lock:
                    if self.connection is connection:
                        self._valid = False
                from .pipeline import pipeline
                pipeline._pub_state()
            raise
        self._record(connection, role, tokens, int((time.monotonic() - start) * 1000))
        return text

    def chat_json(self, system, user, max_tokens=1800, timeout=None, role="answer"):
        for _ in range(2):
            raw = self.chat(system, user, max_tokens, timeout=timeout, role=role, json_mode=True)
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                return json.loads(raw)
            except ValueError:
                user += "\nReturn only valid JSON."
        log.warning("Invalid JSON from AI provider for %s", role)
        return None

    def set_meeting_ctx(self, mid):
        self._tls.mid = mid

    def _record(self, connection, role, tokens, ms):
        from . import db
        from .pipeline import pipeline

        try:
            n_in, n_out, cached = tokens
            db.insert("llm_calls", {"ts": time.time(), "meeting_id": getattr(self._tls, "mid", None) or pipeline.meeting_id,
                                    "role": role, "backend": connection.provider, "model": connection.model,
                                    "tokens_in": n_in, "tokens_out": n_out, "tokens_cached": cached,
                                    "latency_ms": ms, "cost_usd": 0})
        except Exception:
            log.warning("Unable to record local AI usage")
