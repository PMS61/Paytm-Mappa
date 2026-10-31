"""Sarvam chat client for the clarity endpoints: one JSON-mode chat completion, parsed to a dict.

    chat(messages)   POST {SARVAM_URL}/chat/completions, returns the reply's JSON object
    SarvamError      missing key, HTTP / network error, timeout, or a reply that is not a JSON object

Config by env: SARVAM_LLM_API_KEY (a Sarvam API key; falls back to SARVAM_API_KEY), SARVAM_MODEL (default sarvam-105b-conversations: ~1 s a turn; sarvam-105b reasons for ~25 s, too slow for a voice tool).
The Voice Agents key (sk_samvaad_...) used by sarvam_server.py does not work for chat completions (HTTP 403).
"""

import http.client
import json
import os
import re
import urllib.error
import urllib.request

URL = "https://api.sarvam.ai/v1/chat/completions"
TIMEOUT = 30  # seconds
FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class SarvamError(RuntimeError):
    """Anything that went wrong talking to Sarvam. The API turns it into a 502."""


def _post(url: str, body: dict, headers: dict) -> bytes:
    """The one place that touches the network (tests replace it)."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def chat(messages: list[dict]) -> dict:
    """Send `messages` to Sarvam in JSON mode and return the parsed JSON object of the reply."""
    key = (os.environ.get("SARVAM_LLM_API_KEY") or os.environ.get("SARVAM_API_KEY", "")).strip()
    if not key:
        raise SarvamError("SARVAM_LLM_API_KEY (or SARVAM_API_KEY) is not set")
    body = {
        "model": os.environ.get("SARVAM_MODEL", "sarvam-105b-conversations"),
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        # sarvam-105b reasons before answering; on a voice call that costs seconds and can use up the token budget,
        # leaving no content. Low effort plus room for the JSON keeps a turn well inside a 30 s tool timeout.
        "reasoning_effort": os.environ.get("SARVAM_REASONING_EFFORT", "low"),
        "max_tokens": 4096,
    }
    headers = {
        "Content-Type": "application/json",
        "api-subscription-key": key,
        "Authorization": f"Bearer {key}",
    }
    try:
        raw = _post(URL, body, headers)
    except urllib.error.HTTPError as e:
        raise SarvamError(f"Sarvam returned HTTP {e.code}") from e
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as e:
        raise SarvamError(f"Sarvam request failed: {e}") from e
    try:
        text = json.loads(raw)["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise SarvamError("Sarvam reply has no message content") from e
    return parse_json(text)


def parse_json(text: str) -> dict:
    """Parse the model's reply as a JSON object, tolerating a ```json fence around it."""
    if not isinstance(text, str):
        raise SarvamError("Sarvam reply is not text")
    m = FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise SarvamError("Sarvam reply is not valid JSON") from e
    if not isinstance(obj, dict):
        raise SarvamError("Sarvam reply is not a JSON object")
    return obj
