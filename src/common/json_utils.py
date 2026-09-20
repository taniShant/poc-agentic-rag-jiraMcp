"""Helpers for decoding structured JSON returned by local language models."""

from __future__ import annotations

import json
from typing import Any


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract the first valid JSON object from a model response.

    Args:
        text: Raw model response, optionally wrapped in Markdown fences.

    Returns:
        The decoded JSON object.

    Raises:
        ValueError: If no JSON object can be decoded.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()

    try:
        value = json.loads(candidate)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, character in enumerate(candidate):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(candidate[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("model response did not contain a valid JSON object")
