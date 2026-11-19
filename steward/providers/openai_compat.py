"""OpenAI-compatible chat completions over httpx, with hand-written SSE.

This is the one file worth reading twice: it shows exactly what a streaming
tool-call response looks like on the wire.

Wire format (SSE):
    data: {"choices":[{"delta":{"content":"Hel"}}]}
    data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",
           "function":{"name":"read_file","arguments":"{\\"pa"}}]}}]}
    data: {"choices":[{"delta":{"tool_calls":[{"index":0,
           "function":{"arguments":"th\\":\\"a.txt\\"}"}}]}}]}
    data: {"choices":[{"finish_reason":"tool_calls"}]}
    data: [DONE]

Tool arguments arrive as a string split over many chunks and are keyed by
`index`; `id` and `name` usually appear only in the first chunk.
"""

from __future__ import annotations

import json
from typing import Any, cast

import httpx

from steward.config import Config
from steward.providers.base import ProviderError, ProviderResult, TextCallback


class StreamAccumulator:
    """Turn streamed chunks into one result.

    Kept separate from the HTTP code so tests can replay recorded chunks
    without a server.
    """

    def __init__(self) -> None:
        self.text = ""
        self.reasoning = ""
        self.usage: dict[str, Any] = {}
        self._calls: dict[int, dict[str, str]] = {}

    def feed(self, chunk: dict[str, Any]) -> tuple[str, str]:
        """Consume one parsed `data:` object; return (text_delta, reasoning_delta)."""
        usage = chunk.get("usage")
        if isinstance(usage, dict) and usage:
            self.usage = cast("dict[str, Any]", usage)

        choices: list[dict[str, Any]] = chunk.get("choices") or []
        if not choices:
            return "", ""
        delta: dict[str, Any] = choices[0].get("delta") or choices[0].get("message") or {}

        text: str = delta.get("content") or ""
        reasoning: str = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if text:
            self.text += text
        if reasoning:
            self.reasoning += reasoning

        calls: list[dict[str, Any]] = delta.get("tool_calls") or []
        for call in calls:
            index = call.get("index")
            if not isinstance(index, int):
                index = len(self._calls)
            slot = self._calls.setdefault(
                index, {"id": "", "name": "", "arguments": ""}
            )
            if call.get("id"):
                slot["id"] = call["id"]
            function: dict[str, Any] = call.get("function") or {}
            if function.get("name"):
                slot["name"] = function["name"]
            if function.get("arguments"):
                slot["arguments"] += function["arguments"]

        return text, reasoning

    def result(self) -> ProviderResult:
        calls: list[dict[str, Any]] = [self._calls[i] for i in sorted(self._calls)]
        return ProviderResult(
            text=self.text,
            reasoning=self.reasoning,
            tool_calls=calls,
            usage=self.usage,
        )


async def stream_chat(
    config: Config,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    on_text: TextCallback = None,
    on_reasoning: TextCallback = None,
) -> ProviderResult:
    """POST /chat/completions and accumulate the SSE stream."""
    if not config.api_key:
        raise ProviderError("api_key is empty: set it in config.toml or STEWARD_API_KEY")

    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if config.stream_usage:
        payload["stream_options"] = {"include_usage": True}

    url = config.base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    accumulator = StreamAccumulator()
    async with httpx.AsyncClient(timeout=config.timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                raise ProviderError(f"HTTP {response.status_code}: {body[:800]}")

            async for line in response.aiter_lines():
                if not line or line.startswith(":"):
                    continue  # keep-alive comment
                if not line.startswith("data:"):
                    continue  # event:/id:/retry: are not used by chat completions
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    parsed = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(parsed, dict):
                    continue  # only chat.completion chunk objects carry deltas
                text, reasoning = accumulator.feed(cast("dict[str, Any]", parsed))
                if text and on_text:
                    on_text(text)
                if reasoning and on_reasoning:
                    on_reasoning(reasoning)

    return accumulator.result()
