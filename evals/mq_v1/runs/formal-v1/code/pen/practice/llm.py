"""Tool-free JSON LLM helpers for the experimental practice pipeline."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pen import config, providers
from pen.config import LLMConfig
from pen.meter import Meter

JsonLLM = Callable[[str, dict[str, Any]], dict[str, Any]]

_FENCE_RE = re.compile(r"\A```(?:json)?[ \t]*\n(.*)\n```\s*\Z", re.S | re.I)


class LLMJsonError(ValueError):
    """Raised when a model call cannot be interpreted as one JSON object."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object, accepting the common ```json fenced form."""
    text = (raw or "").strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        match = _FENCE_RE.fullmatch(text)
        try:
            data = json.loads(match.group(1)) if match else None
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMJsonError("bad_json", "LLM response was not valid JSON") from exc
        if not match:
            raise LLMJsonError("bad_json", "LLM response was not valid JSON")
    if not isinstance(data, dict):
        raise LLMJsonError("not_object", "LLM response must be a JSON object")
    return data


def usage_dict(usage: Any) -> dict[str, Any]:
    """Convert an OpenAI usage object into a small JSON-compatible dict."""
    if usage is None:
        return {}
    if isinstance(usage, dict):
        raw = usage
    elif hasattr(usage, "model_dump"):
        raw = usage.model_dump()
    else:
        raw = {
            key: getattr(usage, key)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "reasoning_tokens",
            )
            if hasattr(usage, key)
        }
    return {str(k): v for k, v in raw.items() if isinstance(v, int | float | dict)}


def merge_usage(total: dict[str, Any], extra: Any) -> dict[str, Any]:
    """Add numeric usage fields into ``total`` and return it."""
    usage = usage_dict(extra)
    if not usage:
        return total
    total["calls"] = int(total.get("calls", 0)) + 1
    for key, value in usage.items():
        if isinstance(value, int | float):
            total[key] = total.get(key, 0) + value
        elif isinstance(value, dict):
            nested = total.setdefault(key, {})
            if isinstance(nested, dict):
                merge_usage(nested, value)
    return total


class JsonLLMClient:
    """Callable JSON model client with accumulated usage on ``.usage``."""

    def __init__(
        self,
        cfg: LLMConfig,
        *,
        lang: str = "zh",
        meter: Meter | None = None,
        limits: config.RuntimeLimits | None = None,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> None:
        self.cfg = cfg
        self.lang = lang
        self.meter = meter
        self.limits = limits
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.usage: dict[str, Any] = {}
        self.last_response: dict[str, Any] | None = None
        self.last_request: dict[str, Any] | None = None

    def __call__(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
        from openai import OpenAI

        cfg = self.cfg
        client_timeout = self.timeout or (self.limits or config.default_limits()).probe_timeout_s
        client = OpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            timeout=client_timeout,
            max_retries=0,
        )
        clean_payload = {k: v for k, v in payload.items() if k != "_images"}
        from pen.vision import user_message_content, normalize_images
        images = normalize_images(payload.get("_images"))
        if images and not cfg.vision:
            raise LLMJsonError("vision_disabled", "Image input requires a vision-enabled model configuration")
        messages = [
            {
                "role": "system",
                "content": (
                    system.strip()
                    + f"\n\nInterface language: {self.lang}."
                    + "\nReturn exactly one JSON object. Do not call tools."
                ),
            },
            {
                "role": "user",
                "content": user_message_content(json.dumps({"payload": clean_payload}, ensure_ascii=False, sort_keys=True), images),
            },
        ]
        kwargs: dict[str, Any] = {
            "model": cfg.model,
            "messages": providers.flatten_unsigned(messages, cfg.model, cfg.provider),
            "stream": False,
        }
        kwargs.update(providers.thinking_wire(cfg.model, cfg.thinking, cfg.provider))
        if self.max_output_tokens is not None:
            kwargs["max_tokens"] = self.max_output_tokens
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        self.last_request = kwargs
        self.last_response = None
        try:
            resp = client.chat.completions.create(**kwargs)
        finally:
            client.close()
        usage = usage_dict(getattr(resp, "usage", None))
        if self.meter is not None:
            self.meter.add(getattr(resp, "usage", None))
        merge_usage(self.usage, usage)
        self.last_response = resp.model_dump()
        out = parse_json_object(resp.choices[0].message.content or "")
        if usage:
            out["usage"] = usage
        if getattr(resp, "model", None):
            out["model"] = resp.model
        return out


def make_llm(
    cfg: LLMConfig,
    *,
    lang: str = "zh",
    meter: Meter | None = None,
    limits: config.RuntimeLimits | None = None,
    timeout: float | None = None,
) -> JsonLLM:
    """Return a callable ``(system, payload) -> dict`` using no tools."""
    return JsonLLMClient(cfg, lang=lang, meter=meter, limits=limits, timeout=timeout)


def make_json_llm(
    cfg: LLMConfig,
    *,
    lang: str = "zh",
    meter: Meter | None = None,
    limits: config.RuntimeLimits | None = None,
    timeout: float | None = None,
) -> JsonLLM:
    """Backward-compatible name for ``make_llm``."""
    return make_llm(cfg, lang=lang, meter=meter, limits=limits, timeout=timeout)
