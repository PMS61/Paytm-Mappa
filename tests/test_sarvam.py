import json

import pytest

from mappa import sarvam
from mappa.sarvam import SarvamError, chat, parse_json


def _reply(content) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "test-key")
    monkeypatch.delenv("SARVAM_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SARVAM_MODEL", raising=False)


def test_success_returns_parsed_object_and_sends_expected_request(key, monkeypatch):
    seen = {}

    def fake_post(url, body, headers):
        seen.update(url=url, body=body, headers=headers)
        return _reply('{"clarification_needed": true, "clarifications": ["Which amount?"]}')

    monkeypatch.setattr(sarvam, "_post", fake_post)
    out = chat([{"role": "user", "content": "hi"}])
    assert out == {"clarification_needed": True, "clarifications": ["Which amount?"]}
    assert seen["url"] == "https://api.sarvam.ai/v1/chat/completions"
    assert seen["body"]["model"] == "sarvam-105b-conversations"
    assert seen["body"]["reasoning_effort"] == "low"
    assert seen["body"]["temperature"] == 0.2
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["headers"]["api-subscription-key"] == "test-key"
    assert seen["headers"]["Authorization"] == "Bearer test-key"


def test_model_from_env(key, monkeypatch):
    monkeypatch.setenv("SARVAM_MODEL", "sarvam-x")
    seen = {}
    monkeypatch.setattr(sarvam, "_post", lambda u, b, h: (seen.update(b=b), _reply("{}"))[1])
    chat([])
    assert seen["b"]["model"] == "sarvam-x"


def test_fenced_json_is_accepted(key, monkeypatch):
    monkeypatch.setattr(sarvam, "_post", lambda u, b, h: _reply('```json\n{"conclusion": "ok"}\n```'))
    assert chat([]) == {"conclusion": "ok"}


def test_parse_json_plain_fence_and_bad_inputs():
    assert parse_json('```\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(SarvamError):
        parse_json("not json at all")
    with pytest.raises(SarvamError):
        parse_json("[1, 2]")          # JSON, but not an object
    with pytest.raises(SarvamError):
        parse_json(None)


def test_bad_json_reply_raises(key, monkeypatch):
    monkeypatch.setattr(sarvam, "_post", lambda u, b, h: _reply("Sorry, I cannot help"))
    with pytest.raises(SarvamError, match="not valid JSON"):
        chat([])


def test_missing_message_content_raises(key, monkeypatch):
    monkeypatch.setattr(sarvam, "_post", lambda u, b, h: b'{"choices": []}')
    with pytest.raises(SarvamError):
        chat([])
    monkeypatch.setattr(sarvam, "_post", lambda u, b, h: b"<html>")
    with pytest.raises(SarvamError):
        chat([])


def test_http_error_raises(key, monkeypatch):
    import urllib.error

    def boom(u, b, h):
        raise urllib.error.HTTPError(u, 500, "server error", {}, None)

    monkeypatch.setattr(sarvam, "_post", boom)
    with pytest.raises(SarvamError, match="HTTP 500"):
        chat([])


def test_timeout_and_network_errors_raise(key, monkeypatch):
    import urllib.error

    def timeout(u, b, h):
        raise TimeoutError("timed out")

    monkeypatch.setattr(sarvam, "_post", timeout)
    with pytest.raises(SarvamError):
        chat([])

    def unreachable(u, b, h):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(sarvam, "_post", unreachable)
    with pytest.raises(SarvamError):
        chat([])


def test_timeout_is_30_seconds_on_the_real_request(key, monkeypatch):
    import urllib.request

    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _reply("{}")

    def fake_urlopen(req, timeout):
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert chat([]) == {}
    assert seen["timeout"] == 30


def test_missing_key_raises_without_network(monkeypatch):
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.delenv("SARVAM_LLM_API_KEY", raising=False)

    def never(u, b, h):
        raise AssertionError("network must not be touched without a key")

    monkeypatch.setattr(sarvam, "_post", never)
    with pytest.raises(SarvamError, match="SARVAM_API_KEY"):
        chat([])


def test_api_key_never_appears_in_raised_errors(key, monkeypatch):
    import urllib.error

    def unauthorized(u, b, h):
        raise urllib.error.HTTPError(u, 401, "unauthorized", {}, None)

    monkeypatch.setattr(sarvam, "_post", unauthorized)
    with pytest.raises(SarvamError) as ei:
        chat([])
    assert "test-key" not in str(ei.value)
    assert "test-key" not in repr(ei.value.__cause__)


def test_null_content_raises(key, monkeypatch):
    monkeypatch.setattr(sarvam, "_post", lambda u, b, h: _reply(None))
    with pytest.raises(SarvamError, match="not text"):
        chat([])


def test_empty_body_raises(key, monkeypatch):
    monkeypatch.setattr(sarvam, "_post", lambda u, b, h: b"")
    with pytest.raises(SarvamError):
        chat([])


def test_real_post_sends_json_body_and_auth_headers(key, monkeypatch):
    import urllib.request

    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _reply("{}")

    def fake_urlopen(req, timeout):
        seen["method"] = req.get_method()
        seen["url"] = req.full_url
        seen["data"] = json.loads(req.data)
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    msgs = [{"role": "user", "content": "refund 4000"}]
    assert chat(msgs) == {}
    assert seen["method"] == "POST"
    assert seen["url"] == "https://api.sarvam.ai/v1/chat/completions"
    assert seen["data"]["messages"] == msgs
    assert seen["headers"]["content-type"] == "application/json"
    assert seen["headers"]["api-subscription-key"] == "test-key"
    assert seen["headers"]["authorization"] == "Bearer test-key"


def test_truncated_response_is_wrapped_in_sarvam_error(key, monkeypatch):
    import http.client
    import urllib.request

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise http.client.IncompleteRead(b'{"choices"')

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Resp())
    with pytest.raises(SarvamError):
        chat([])
