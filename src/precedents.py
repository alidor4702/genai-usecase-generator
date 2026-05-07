"""Precedent corpus loading and retrieval.

The full precedent corpus (~2.1k entries) is loaded into a single numpy float32
matrix at process start. Cosine similarity against the matrix is sub-millisecond
at this scale; no vector database is needed.

Retrieval pipeline:
    1. Industry filter (company.industry → precedent.industry, case-insensitive substring)
    2. Depth filter (precedent.depth_score >= min_depth)
    3. Exclude target company's own entries (avoid recommending what they already do)
    4. Cosine rank
    5. Optional MMR diversification when top-k scores are clustered

This is the file every downstream activity (retrieve, generate, score) calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

import numpy as np

from src.db import load_all_precedents_for_index
from src.models import Precedent, RetrievedPrecedents

logger = logging.getLogger(__name__)


@dataclass
class CorpusIndex:
    """In-memory representation of the precedent corpus."""

    precedents: list[Precedent]
    matrix: np.ndarray  # shape (N, D) float32, L2-normalized
    industry_lower: list[str]  # parallel to precedents
    company_norm: list[str]  # parallel to precedents — lowered+stripped company name
    depth: np.ndarray  # parallel; depth in [0, 1], NaN if unscored

    @property
    def size(self) -> int:
        return len(self.precedents)


_INDEX: CorpusIndex | None = None
_INDEX_LOCK = asyncio.Lock()


def _l2_normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def _normalize_company(name: str) -> str:
    s = re.sub(r"[^\w]+", "", name.lower())
    return s


async def load_index(force: bool = False) -> CorpusIndex:
    global _INDEX
    async with _INDEX_LOCK:
        if _INDEX is not None and not force:
            return _INDEX

        rows = await load_all_precedents_for_index()
        if not rows:
            raise RuntimeError("Precedent corpus is empty. Run `scripts.build_data all` first.")

        precedents: list[Precedent] = []
        vectors: list[np.ndarray] = []
        industries: list[str] = []
        companies: list[str] = []
        depths: list[float] = []

        for r in rows:
            try:
                emb = json.loads(str(r["embedding"]))
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(emb, list) or not emb:
                continue
            precedents.append(
                Precedent(
                    id=str(r["id"]),
                    company=str(r["company"]),
                    industry=str(r["industry"] or ""),
                    title=str(r["title"]),
                    description=str(r["description"]),
                    outcome=r.get("outcome"),
                    deep_content=r.get("deep_content"),
                    source_url=r.get("source_url"),
                    source=r.get("source"),  # type: ignore[arg-type]
                    embedding=None,  # we keep vectors in the matrix, not in Precedent
                )
            )
            vectors.append(np.asarray(emb, dtype=np.float32))
            industries.append(str(r.get("industry") or "").lower())
            companies.append(_normalize_company(str(r["company"])))
            ds = r.get("depth_score")
            depths.append(float(ds) if ds is not None else float("nan"))

        if not vectors:
            raise RuntimeError("No precedents have embeddings; run `scripts.build_data embed`")

        matrix = _l2_normalize(np.vstack(vectors))
        _INDEX = CorpusIndex(
            precedents=precedents,
            matrix=matrix,
            industry_lower=industries,
            company_norm=companies,
            depth=np.asarray(depths, dtype=np.float32),
        )
        logger.info(
            "precedents: loaded %d entries (%d-dim) | %d with depth scores",
            _INDEX.size,
            matrix.shape[1],
            int(np.sum(~np.isnan(_INDEX.depth))),
        )
        return _INDEX


_INDUSTRY_STOPWORDS = {
    "services", "service", "management", "industry", "industries",
    "products", "solutions", "company", "companies", "platform", "platforms",
    "technology", "tech", "business", "businesses", "global", "international",
    "group", "groups", "holdings", "the", "and", "for", "with",
}


def _industry_match_mask(idx: CorpusIndex, company_industries: list[str]) -> np.ndarray:
    """True where precedent.industry overlaps any meaningful keyword from the
    company's industry / sub-industries.

    Stopwords (e.g. "services", "management") are filtered so the mask doesn't
    collapse onto every "X Services" precedent in the corpus.
    """
    if not company_industries:
        return np.ones(idx.size, dtype=bool)
    keywords: set[str] = set()
    for ci in company_industries:
        for tok in re.split(r"[,&/]+|\s+", ci.lower()):
            tok = tok.strip()
            if len(tok) >= 4 and tok not in _INDUSTRY_STOPWORDS:
                keywords.add(tok)
    if not keywords:
        return np.ones(idx.size, dtype=bool)
    mask = np.zeros(idx.size, dtype=bool)
    for i, ind in enumerate(idx.industry_lower):
        if any(k in ind for k in keywords):
            mask[i] = True
    return mask


def _exclude_target_company_mask(idx: CorpusIndex, target_company: str) -> np.ndarray:
    """True for entries that are NOT the target company itself.

    Uses normalized substring match — `Stellantis` would exclude any precedent whose
    normalized company name contains `stellantis` (Stellantis France, Stellantis NV).
    """
    target_norm = _normalize_company(target_company)
    if not target_norm:
        return np.ones(idx.size, dtype=bool)
    mask = np.array([target_norm not in c for c in idx.company_norm], dtype=bool)
    return mask


def _depth_mask(idx: CorpusIndex, min_depth: float) -> np.ndarray:
    """Keep entries with depth_score >= min_depth. NaN (unscored) is treated as 0.5
    so we don't lose unscored entries entirely if depth scoring hasn't run yet."""
    depth = np.where(np.isnan(idx.depth), 0.5, idx.depth)
    return depth >= min_depth


def _mmr_select(
    matrix: np.ndarray,
    candidate_indices: np.ndarray,
    similarities: np.ndarray,
    k: int,
    lambda_param: float = 0.7,
) -> list[int]:
    """Maximal Marginal Relevance selection.

    Trade off relevance to query (sim) vs. diversity from already-picked items.
    `lambda_param` close to 1 = pure relevance; close to 0 = pure diversity.
    """
    selected: list[int] = []
    selected_set: set[int] = set()
    available = list(candidate_indices)
    sim_to_query = {int(i): float(s) for i, s in zip(candidate_indices, similarities, strict=False)}

    while len(selected) < k and available:
        best = available[0]
        best_score = -1e9
        for cand in available:
            div = 0.0
            if selected:
                # max similarity to anything already picked
                vecs_selected = matrix[selected]
                div = float(np.max(matrix[cand] @ vecs_selected.T))
            score = lambda_param * sim_to_query[cand] - (1.0 - lambda_param) * div
            if score > best_score:
                best_score = score
                best = cand
        selected.append(int(best))
        selected_set.add(int(best))
        available = [a for a in available if a not in selected_set]
    return selected


async def retrieve_top_k(
    query_embedding: list[float],
    *,
    k: int = 6,
    target_company: str = "",
    company_industries: list[str] | None = None,
    min_depth: float = 0.4,
    use_mmr: bool = True,
    mmr_threshold: float = 0.05,  # apply MMR if top-k similarities are within this band
) -> RetrievedPrecedents:
    """Return the top-k most relevant precedents for the given company embedding.

    Filtering order:
      1. Industry overlap (if any company industries provided)
      2. Depth floor
      3. Exclude target company's own entries
    Then cosine rank, optional MMR diversification on near-tied scores.
    """
    idx = await load_index()
    q = np.asarray(query_embedding, dtype=np.float32)
    q = q / (np.linalg.norm(q) or 1.0)

    industry_mask = _industry_match_mask(idx, company_industries or [])
    depth_mask = _depth_mask(idx, min_depth)
    company_mask = _exclude_target_company_mask(idx, target_company)
    base_mask = industry_mask & depth_mask & company_mask

    if not base_mask.any():
        # Relax industry filter rather than return nothing
        logger.info("retrieve: industry filter too restrictive, relaxing")
        base_mask = depth_mask & company_mask
    if not base_mask.any():
        # Relax depth filter as a last resort
        logger.info("retrieve: depth filter too restrictive, relaxing")
        base_mask = company_mask

    candidate_indices = np.flatnonzero(base_mask)
    if candidate_indices.size == 0:
        return RetrievedPrecedents(items=[], similarity_scores=[], used_mmr=False)

    sims = idx.matrix[candidate_indices] @ q
    order = np.argsort(-sims)
    top_n = min(k * 4, candidate_indices.size)
    top_indices = candidate_indices[order[:top_n]]
    top_sims = sims[order[:top_n]]

    used_mmr = False
    if use_mmr and top_indices.size > k:
        spread = float(top_sims[:k].max() - top_sims[:k].min())
        if spread < mmr_threshold:
            selected = _mmr_select(idx.matrix, top_indices, top_sims, k=k, lambda_param=0.7)
            used_mmr = True
        else:
            selected = list(top_indices[:k].tolist())
    else:
        selected = list(top_indices[:k].tolist())

    items: list[Precedent] = [idx.precedents[i] for i in selected]
    selected_sims: list[float] = [
        float(idx.matrix[i] @ q)
        for i in selected  # recompute to align order
    ]
    return RetrievedPrecedents(items=items, similarity_scores=selected_sims, used_mmr=used_mmr)
