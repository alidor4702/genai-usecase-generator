"""Helpers to build EvidenceItem entries with stable deduplicating IDs.

The ledger lives as Pydantic types in src/models.py. This module is the
construction layer — every place that wants to record a source calls one of
these factories so IDs are consistent across the pipeline (same URL + title
always hashes to the same id, regardless of which step recorded it).
"""

from __future__ import annotations

import hashlib

from src.models import (
    EvidenceItem,
    EvidenceKind,
    ExistingInitiative,
    NewsItem,
    Precedent,
    WikipediaFacts,
)


def evidence_id_for(url: str | None, title: str) -> str:
    """Stable 10-char hash; the ev- prefix is what enrichment cites in claims."""
    raw = f"{(url or '').strip()}|{title.strip()}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return f"ev-{h}"


def from_wikipedia(facts: WikipediaFacts, company_name: str) -> EvidenceItem | None:
    if not facts.found or not facts.summary:
        return None
    title = f"Wikipedia: {company_name}"
    url = (
        f"https://en.wikipedia.org/wiki/{company_name.replace(' ', '_')}"
        if company_name
        else None
    )
    return EvidenceItem(
        id=evidence_id_for(url, title),
        source_kind=EvidenceKind.WIKIPEDIA,
        url=url,
        title=title,
        content=facts.summary,
        fetched_at_step="research",
        confidence="high",
    )


def from_news_item(n: NewsItem) -> EvidenceItem:
    body = n.deep_content or n.snippet or ""
    return EvidenceItem(
        id=evidence_id_for(n.url, n.title),
        source_kind=EvidenceKind.NEWS,
        url=n.url,
        title=n.title or "(untitled news)",
        content=body,
        fetched_at_step="research",
        confidence="medium",
    )


def from_existing_initiative(ei: ExistingInitiative) -> EvidenceItem | None:
    if not ei.source_url and not ei.description:
        return None
    title = ei.description[:120] if ei.description else "(existing initiative)"
    return EvidenceItem(
        id=evidence_id_for(ei.source_url, title),
        source_kind=EvidenceKind.EXISTING_INITIATIVE,
        url=ei.source_url,
        title=title,
        content=ei.description,
        fetched_at_step="research",
        confidence="medium" if ei.confidence == "medium" else ei.confidence,  # type: ignore[arg-type]
    )


def from_precedent(p: Precedent) -> EvidenceItem:
    body = p.deep_content or p.description or ""
    return EvidenceItem(
        id=evidence_id_for(p.source_url, p.title),
        source_kind=EvidenceKind.PRECEDENT,
        url=p.source_url,
        title=f"{p.company} — {p.title}",
        content=body,
        fetched_at_step="retrieve",
        confidence="high",
    )


def from_tavily_result(
    url: str,
    title: str,
    content: str,
    *,
    kind: EvidenceKind = EvidenceKind.TAVILY,
    fetched_at_step: str = "research",
    confidence: str = "medium",
) -> EvidenceItem:
    return EvidenceItem(
        id=evidence_id_for(url, title),
        source_kind=kind,
        url=url or None,
        title=title or "(untitled)",
        content=content,
        fetched_at_step=fetched_at_step,
        confidence=confidence,  # type: ignore[arg-type]
    )
