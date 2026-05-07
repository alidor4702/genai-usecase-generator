"""Normalization helpers for precedent corpus entries.

- Vendor-name stripping: per docs/architecture.md, vendor product names from
  competing clouds are stripped during preprocessing so the generator does not
  accidentally recommend non-Mistral products to customers.
- Stable ID generation via short hash of (source, company, title) so re-runs
  are idempotent.
"""

from __future__ import annotations

import hashlib
import re

VENDOR_TERMS = (
    # Google Cloud
    "Vertex AI",
    "BigQuery",
    "Gemini",
    "Google Cloud Platform",
    "Google Cloud",
    "GCP",
    "Cloud Run",
    "Cloud Functions",
    "Cloud SQL",
    "Pub/Sub",
    # AWS
    "Amazon Bedrock",
    "Bedrock",
    "Amazon SageMaker",
    "SageMaker",
    "AWS Lambda",
    "Lambda",
    "Amazon Kendra",
    "AWS",
    # Azure
    "Azure OpenAI",
    "Azure AI",
    "Azure Cognitive",
    "Azure",
    # Other LLM providers — strip so the system doesn't recommend them
    "OpenAI",
    "GPT-4",
    "GPT-3",
    "Claude",
    "Anthropic",
    "Cohere",
    "Llama",
    "Meta AI",
    "PaLM",
)


def strip_vendor_terms(text: str) -> str:
    """Remove vendor product names so the generator stays vendor-agnostic.

    Replaces with a generic placeholder so sentence structure survives.
    """
    if not text:
        return text
    out = text
    # Sort longest-first so "Google Cloud Platform" gets stripped before "Google Cloud"
    for term in sorted(VENDOR_TERMS, key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(term)}\b", "[PROVIDER]", out, flags=re.IGNORECASE)
    # Collapse repeated placeholders and surrounding whitespace
    out = re.sub(r"(\[PROVIDER\][\s,]*){2,}", "[PROVIDER] ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def make_id(source: str, company: str, title: str) -> str:
    """Stable short ID for a precedent entry. Idempotent across runs."""
    h = hashlib.sha1(f"{source}|{company}|{title}".lower().encode("utf-8")).hexdigest()
    return f"{source}-{h[:10]}"
