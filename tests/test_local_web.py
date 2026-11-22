import pathlib
import os
import tempfile
import unittest

from test_data_layer import run


class LocalWebTests(unittest.TestCase):
    def test_capture_payload_and_origin(self):
        with tempfile.TemporaryDirectory() as data_dir:
            result = run("""
import json
import struct
from fastapi.testclient import TestClient
from app.server import app, pipeline

client = TestClient(app, base_url="http://127.0.0.1:8765")
received = []
def receive(mid, samples):
    if mid != 7:
        return False
    received.extend(samples.tolist())
    return True
pipeline.receive_audio = receive
route = "/api/meeting/7/capture"
pcm = struct.pack("<hhhh", -32768, 16384, 0, -8192)
print(json.dumps({
    "status": client.post(route, content=pcm).status_code,
    "pcm": received,
    "foreign_origin": client.post(route, content=pcm, headers={"Origin": "http://example.org"}).status_code,
    "foreign_host": client.post(route, content=pcm, headers={"Host": "example.org"}).status_code,
    "invalid": client.post(route, content=b"x").status_code,
    "oversized": client.post(route, content=b"x" * 64004).status_code,
    "stale": client.post("/api/meeting/8/capture", content=pcm).status_code,
    "account": client.get("/api/account").status_code,
}))
""", pathlib.Path(data_dir))
        self.assertEqual(result, {
            "status": 200, "pcm": [[-1, 0.5], [0, -0.25]], "foreign_origin": 403,
            "foreign_host": 421, "invalid": 400, "oversized": 413, "stale": 409, "account": 404,
        })

    def test_provider_endpoints_and_zen_protocols(self):
        with tempfile.TemporaryDirectory() as data_dir:
            result = run("""
import json
import httpx
from app.llm import LLM, _connection

calls = []
def reply(request):
    body = json.loads(request.content)
    calls.append({"host": request.url.host, "path": request.url.path,
                  "auth": bool(request.headers.get("authorization") or request.headers.get("x-api-key")),
                  "model": body["model"]})
    if request.url.path.endswith("/messages"):
        return httpx.Response(200, json={"content": [{"type": "text", "text": "OK"}]})
    if request.url.path.endswith("/responses"):
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]})
    return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

llm = LLM()
llm._client = httpx.Client(transport=httpx.MockTransport(reply))
for provider in ("grok", "kimi", "kimi_cn", "deepseek", "openrouter",
                 "opencode_chat", "opencode_responses", "opencode_messages"):
    connection = _connection({"provider": provider, "model": "example", "api_key": "test-key"})
    llm._test_connection(connection)
print(json.dumps(calls))
""", pathlib.Path(data_dir))
        self.assertEqual([(x["host"], x["path"]) for x in result], [
            ("api.x.ai", "/v1/chat/completions"),
            ("api.moonshot.ai", "/v1/chat/completions"),
            ("api.moonshot.cn", "/v1/chat/completions"),
            ("api.deepseek.com", "/chat/completions"),
            ("openrouter.ai", "/api/v1/chat/completions"),
            ("opencode.ai", "/zen/v1/chat/completions"),
            ("opencode.ai", "/zen/v1/responses"),
            ("opencode.ai", "/zen/v1/messages"),
        ])
        self.assertTrue(all(call["auth"] and call["model"] == "example" for call in result))

    def test_invalid_connection_keeps_ai_disabled(self):
        with tempfile.TemporaryDirectory() as data_dir:
            result = run("""
import json
import httpx
from fastapi.testclient import TestClient
from app.server import app, pipeline

client = TestClient(app, base_url="http://127.0.0.1:8765")
pipeline.llm._client = httpx.Client(transport=httpx.MockTransport(
    lambda request: httpx.Response(401, json={"error": "invalid"})))
response = client.put("/api/ai/connection", json={
    "provider": "openai", "model": "test-model", "api_key": "invalid-key",
})
print(json.dumps({"error": response.json()["error"],
                  "available": pipeline.llm.available,
                  "configured": pipeline.llm.settings()["configured"]}))
""", pathlib.Path(data_dir))
        self.assertEqual(result, {"error": "http_401", "available": False, "configured": False})

    def test_ai_providers_and_key_isolation(self):
        with tempfile.TemporaryDirectory() as data_dir:
            result = run("""
import json
import os
import zipfile
import httpx
from fastapi.testclient import TestClient
from app import config, exporter
from app.llm import LLM
from app.server import app, pipeline

client = TestClient(app, base_url="http://127.0.0.1:8765")
route = "/api/ai/connection"
requests = []
def provider(request):
    body = json.loads(request.content)
    requests.append({
        "host": request.url.host, "path": request.url.path,
        "auth": bool(request.headers.get("Authorization") or request.headers.get("x-api-key")),
        "model": body["model"],
    })
    if request.url.host == "api.anthropic.com":
        return httpx.Response(200, json={"content": [{"type": "text", "text": "OK"}]})
    return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

pipeline.llm._client = httpx.Client(transport=httpx.MockTransport(provider))
captured = []
for kind in ("openai", "anthropic", "gemini", "compatible"):
    body = {"provider": kind, "model": "test-model", "api_key": "fixture-key"}
    if kind == "compatible":
        body["base_url"] = "http://127.0.0.1:11434/v1"
    captured.append(client.post("/api/ai/test", json=body).json())
    captured.append(client.put(route, json=body).json())
captured.append(client.get(route).json())
captured.append(client.get("/api/settings").json())
captured.append(client.put(route, json={"api_key": ["fixture-key"]}).json())
captured.append(client.post("/api/ai/test", json={
    "provider": "compatible", "model": "test-model", "base_url": "https://example.org/v1",
}).json())

path = config.DATA_DIR / "ai-connection.json"
mode = os.stat(path).st_mode & 0o777
archive = config.DATA_DIR / "out.deban"
exporter.export_archive(archive)
with zipfile.ZipFile(archive) as z:
    exported = b"fixture-key" in b"".join(z.read(name) for name in z.namelist() if not name.endswith("/"))
print(json.dumps({
    "requests": requests, "ok": sum(item.get("ok", False) for item in captured),
    "exposed": "fixture-key" in json.dumps(captured), "exported": exported,
    "changed_endpoint": captured[-1]["error"], "mode": mode,
    "restored": LLM().connection is not None, "cleared": not client.delete(route).json()["configured"],
    "file_removed": not path.exists(),
}))
""", pathlib.Path(data_dir))
        self.assertEqual(result["ok"], 4)
        self.assertEqual([item["host"] for item in result["requests"]], [
            "api.openai.com", "api.openai.com", "api.anthropic.com", "api.anthropic.com",
            "generativelanguage.googleapis.com", "generativelanguage.googleapis.com", "127.0.0.1", "127.0.0.1",
        ])
        self.assertTrue(all(item["auth"] and item["model"] == "test-model" for item in result["requests"]))
        self.assertFalse(result["exposed"])
        self.assertFalse(result["exported"])
        self.assertEqual(result["changed_endpoint"], "missing_key")
        if os.name != "nt":
            self.assertEqual(result["mode"], 0o600)
        self.assertTrue(result["restored"])
        self.assertTrue(result["cleared"])
        self.assertTrue(result["file_removed"])
