"""Pipeline-internal utilities shared across activities.

Kept tiny on purpose — anything that grows beyond a few helper functions
should get its own module.
"""

from __future__ import annotations


def strip_fence(s: str) -> str:
    """Strip leading/trailing markdown code fences from an LLM response.

    Mistral's `response_format={"type": "json_object"}` mostly produces
    bare JSON, but the model occasionally wraps it in a ```json fence
    anyway. Every activity that parses LLM JSON ran a near-identical
    7-line strip-fence helper. This is the shared one.

    Handles both ```json...``` and ```...``` (no language tag) forms.
    Returns the input unchanged if no fence is present.
    """
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()
