"""Extra kwargs for OpenAI Responses API calls that depend on the model family."""

from typing import Any


def responses_api_kwargs(model: str) -> dict[str, Any]:
    """GPT-6 reasoning models reject ``temperature``; use ``reasoning.effort`` instead."""
    if model.startswith("gpt-6-"):
        return {"reasoning": {"effort": "none"}}
    return {"temperature": 0}
