"""Two-tier credibility classifier for the web-verify rescue layer.

The fact-checker (meta-eval) flags many real claims as unsupported simply
because the evidence pool the pipeline gathered didn't happen to include the
specific source. This module provides the credibility gate the post-meta-eval
web-verify step uses to decide whether a Tavily result is good enough to
promote a claim from "unsupported" to "supported".

Two tiers:
- TIER 1 (`verified`): the source is on the curated allowlist —
  company-official domains (TLDs derived from company name), a handful of
  major business-news outlets (FT, Reuters, Bloomberg, Le Monde, WSJ, etc.),
  and government / EU regulatory domains. Strong precision, defensible in
  the writeup.
- TIER 2 (`corroborated`): the domain isn't on the allowlist but the result's
  text contains both an entity match (a named token from the claim, e.g.
  "GreenUp", "ModiFace", "Centric Software", "5,000 stores") and the
  numeric/keyword anchor from the original claim. Lower precision but
  catches real claims served from blogs, Medium, conference recap pages, etc.

Both tiers promote the claim to supported; the report renders them with
distinct styling so a reviewer can see *how* a claim was rescued.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Curated major-business-news + reference domains. Lowercase; we match by
# suffix so "www.reuters.com" and "uk.reuters.com" both qualify.
_ALLOWLIST_DOMAINS: tuple[str, ...] = (
    # Top-tier business news
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "economist.com",
    "nytimes.com",
    "washingtonpost.com",
    "forbes.com",
    "fortune.com",
    "businessinsider.com",
    "cnbc.com",
    "hbr.org",
    "mit.edu",
    # European business press
    "lemonde.fr",
    "lefigaro.fr",
    "lesechos.fr",
    "challenges.fr",
    "handelsblatt.com",
    "faz.net",
    "spiegel.de",
    "elpais.com",
    "expansion.com",
    "ilsole24ore.com",
    "corriere.it",
    "telegraph.co.uk",
    "guardian.co.uk",
    "theguardian.com",
    "bbc.com",
    "bbc.co.uk",
    # Tech / industry trade press
    "techcrunch.com",
    "theverge.com",
    "wired.com",
    "venturebeat.com",
    "arstechnica.com",
    # Reference + neutral knowledge bases
    "wikipedia.org",
    "wikimedia.org",
    "crunchbase.com",
    # Industry analyst houses (when their content is on the open web)
    "gartner.com",
    "forrester.com",
    "idc.com",
    "mckinsey.com",
    "bcg.com",
    "deloitte.com",
)

# Government / regulator TLDs and known regulatory domains.
_GOVERNMENT_DOMAIN_SUFFIXES: tuple[str, ...] = (
    ".gov",
    ".gov.uk",
    ".gouv.fr",
    ".europa.eu",
    ".gc.ca",
    ".gov.au",
    ".gov.sg",
)

# Specific EU/regulatory domains worth allowing explicitly.
_REGULATORY_ALLOWLIST: tuple[str, ...] = (
    "europa.eu",
    "ec.europa.eu",
    "ecb.europa.eu",
    "esma.europa.eu",
    "eba.europa.eu",
    "eiopa.europa.eu",
    "edpb.europa.eu",
    "iso.org",
    "nist.gov",
)


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _is_allowlisted(host: str) -> bool:
    if not host:
        return False
    for d in _ALLOWLIST_DOMAINS + _REGULATORY_ALLOWLIST:
        if host == d or host.endswith("." + d):
            return True
    for suffix in _GOVERNMENT_DOMAIN_SUFFIXES:
        if host.endswith(suffix):
            return True
    return False


def _is_company_domain(host: str, company_name: str) -> bool:
    """Cheap heuristic: company-official sites usually have the company name
    (or a tokenized form) in the registrable domain. e.g. carrefour.com,
    veolia.com, loreal.com, mistral.ai. Not perfect (Carrefour also runs
    carrefour-banque.fr) but catches the majority cleanly.
    """
    if not host or not company_name:
        return False
    name = re.sub(r"[^a-z0-9]+", "", company_name.lower())
    if not name or len(name) < 3:
        return False
    # Strip common corporate suffixes from the comparison.
    name = re.sub(r"(group|sa|ag|plc|inc|corp|company|ltd)$", "", name) or name
    return name in re.sub(r"[^a-z0-9]+", "", host)


def classify_source(url: str, company_name: str) -> str | None:
    """Return "verified" / "corroborated" / None for a result URL.

    "verified" — domain is on the allowlist, is government/regulator, or is
    the company's own official domain.
    "corroborated" — caller will further check entity+number match before
    promoting.
    None — neither (caller treats the result as not credible).
    """
    host = _hostname(url)
    if not host:
        return None
    if _is_allowlisted(host) or _is_company_domain(host, company_name):
        return "verified"
    # Anything else passes to the corroboration tier — caller still needs to
    # check entity/number match before promoting.
    return "corroborated"


_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:%|m|b|k|bn|million|billion|thousand|pb|tb|gb)?\b", re.IGNORECASE)


def claim_anchor_present(claim: str, body: str) -> bool:
    """Cheap entity+number sanity check for the corroboration tier.

    The body must contain at least one of:
    - A numeric token from the claim (matching tolerantly: "10 PB" matches
      "10-petabyte"; "8-15%" matches either "8" or "15" near "%").
    - One of the claim's named-entity tokens (capitalized words ≥4 chars,
      excluding common stopwords).
    """
    if not claim or not body:
        return False
    body_lc = body.lower()

    # Numeric token check.
    claim_numbers = _NUMBER_RE.findall(claim)
    for n in claim_numbers:
        digits = re.sub(r"[^\d]", "", n)
        if digits and digits in re.sub(r"[^\d]", "", body):
            return True

    # Named-entity check: capitalized tokens of length ≥4 in the original
    # claim, lowercased and looked up in the body.
    cap_tokens = re.findall(r"\b[A-Z][a-zA-Z0-9]{3,}\b", claim)
    stopwords = {"this", "that", "these", "those", "company", "corporate"}
    for tok in cap_tokens:
        t = tok.lower()
        if t in stopwords:
            continue
        if t in body_lc:
            return True
    return False
