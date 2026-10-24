"""Chat-completions client for the providers in config/llm.yaml. Only the TAMU gateway (OpenAI-compatible) today."""

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

Content = list[dict[str, Any]]


class LLMError(RuntimeError):
    pass


class Truncated(LLMError):
    """The reply hit the route's max_tokens (finish_reason "length"): the JSON is cut off mid-card. Asking again for
    the same thing gets the same cut, so the caller sends less instead (pipeline: half the pages)."""


class RateLimited(LLMError):
    """The provider is throttling us. Not the document's fault: the job waits for the next poll without using up a
    try (pipeline.charged)."""


RETRYABLE = (408, 429, 500, 502, 503, 504, 524)
RATE_WAITS = (30, 60, 120)    # seconds; the gateway's limit is per minute, so short sleeps just hit it again
COOLDOWN_S = 600              # still throttled after every wait (10-07: a quota, not a burst): stop asking for a while


def rate_limited(status: int, body: str) -> bool:
    """A 429, or the TAMU gateway's version of one: HTTP 400 whose body says "Rate Limit Reached"."""
    return status == 429 or "rate limit" in body.lower()


class LLM(Protocol):
    async def complete(self, route: str, system: str, content: Content) -> str: ...


@dataclass(frozen=True)
class Route:
    base_url: str
    api_key: str
    model: str
    max_tokens: int
    user_agent: str
    images: bool
    timeout_s: float
    stream: bool = False      # stream the reply: the gateway's proxy cuts a silent request off at 100 s (HTTP 524)


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def image(data: bytes) -> dict[str, Any]:
    mime = "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode()}}


def _secret(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise LLMError(f"missing secret file {path}")
    return p.read_text(encoding="utf-8").strip()


class Router:
    def __init__(self, config: Path) -> None:
        self.config: dict[str, Any] = yaml.safe_load(config.read_text(encoding="utf-8"))
        self.cooldown_until = 0.0     # monotonic time; after a rate limit outlasts every wait, calls fail fast till then

    def route(self, name: str) -> Route:
        r = self.config["routes"][name]
        p = self.config["providers"][r["provider"]]
        return Route(_secret(p["base_url_file"]).rstrip("/"), _secret(p["api_key_file"]), r["model"],
                     int(r.get("max_tokens", 8000)), p.get("user_agent", "mnemosyne/1.0"), bool(p.get("images", True)),
                     float(p.get("timeout_s", 300)), bool(r.get("stream", False)))

    def images_allowed(self, name: str) -> bool:
        return bool(self.config["providers"][self.config["routes"][name]["provider"]].get("images", True))

    async def complete(self, route: str, system: str, content: Content) -> str:
        r = self.route(route)
        if time.monotonic() < self.cooldown_until:
            raise RateLimited(f"{route} via {r.model}: still throttled; waiting before asking the gateway again")
        if not r.images:
            content = [c for c in content if c["type"] == "text"]
        payload = {"model": r.model, "stream": r.stream, "max_tokens": r.max_tokens,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        headers = {"Authorization": f"Bearer {r.api_key}", "User-Agent": r.user_agent}
        last, limited = "", False
        for attempt in range(len(RATE_WAITS) + 1):
            async with httpx.AsyncClient(timeout=r.timeout_s) as http:
                if r.stream:
                    async with http.stream("POST", f"{r.base_url}/chat/completions", json=payload,
                                           headers=headers) as resp:
                        if resp.status_code == 200:
                            out, reasons = stream_reply([line async for line in resp.aiter_lines()])
                            if "length" in reasons:
                                raise Truncated(f"{route} via {r.model}: the reply hit max_tokens {r.max_tokens}")
                            return out
                        status, body_text = resp.status_code, (await resp.aread()).decode(errors="replace")
                else:
                    resp = await http.post(f"{r.base_url}/chat/completions", json=payload, headers=headers)
                    if resp.status_code == 200:
                        body: dict[str, Any] = resp.json()
                        choice: dict[str, Any] = body["choices"][0]
                        if choice.get("finish_reason") == "length":
                            raise Truncated(f"{route} via {r.model}: the reply hit max_tokens {r.max_tokens}")
                        return str(choice["message"]["content"] or "")
                    status, body_text = resp.status_code, resp.text
            last = f"HTTP {status}: {body_text[:300]}"
            limited = rate_limited(status, body_text)
            if attempt == len(RATE_WAITS) or not (limited or status in RETRYABLE):
                break
            await asyncio.sleep(RATE_WAITS[attempt] if limited else 10 * (attempt + 1))
        if limited:
            self.cooldown_until = time.monotonic() + COOLDOWN_S
            raise RateLimited(f"{route} via {r.model}: {last}")
        raise LLMError(f"{route} via {r.model}: {last}")


def streamed(lines: list[str]) -> str:
    """The reply's text from server-sent chat-completion chunks ('data: {...}' lines, ending with 'data: [DONE]')."""
    return stream_reply(lines)[0]


def stream_reply(lines: list[str]) -> tuple[str, set[str]]:
    """The text and every finish_reason seen. The TAMU gateway sends "length" when max_tokens cut the reply, then a
    closing "stop" anyway (2026-10-20), so look for "length" among them, not at the last one."""
    out: list[str] = []
    reasons: set[str] = set()
    for line in lines:
        data = line.removeprefix("data:").strip() if line.startswith("data:") else ""
        if not data or data == "[DONE]":
            continue
        chunk: dict[str, Any] = json.loads(data)
        choices: list[dict[str, Any]] = chunk.get("choices") or []
        for choice in choices:
            delta: dict[str, Any] = choice.get("delta") or choice.get("message") or {}
            out.append(str(delta.get("content") or ""))
            if choice.get("finish_reason"):
                reasons.add(str(choice["finish_reason"]))
    return "".join(out), reasons
