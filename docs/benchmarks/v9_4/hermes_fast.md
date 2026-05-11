> **Draft — needs revision before customer use.** Meta-eval confidence `0.78` (sales-engineer-ready threshold ≥ 0.70). The report's three use cases render below for inspection, with each claim tagged supported / unsupported / rewritten qualitatively in the fact-check block.
>
> **Cross-cutting concern:** All three use cases rely heavily on the company context's claim about Hermès' AI Governance Committee, but this claim is only supported by a single LinkedIn post (ev-c0a3c2edfb) with no corroborating official Hermès sources. The committee's existence, scope, and priorities are critical to all proposals but are under-documented.
>
> **Weakest use case:** Lacks any cited evidence or precedents to support the claim that Hermès' products (e.g., Birkin, Kelly) are prime targets for counterfeiting or that the resale market is a growing concern. The use case also fails to ground its assumptions about Hermès' proprietary product specifications or the feasibility of vision-language verification for luxury goods.

## GenAI Use Cases for Hermes

Three customer-ready use cases, scored against the Mistral Proto Team's five-criteria rubric (relevance · iconic potential · estimated impact · feasibility · Mistral suitability) and verified against Hermes's existing AI initiatives. Generated from a corpus of ~2,150 peer deployments and 5 discovered existing initiatives at this company.

_Industry: Unknown. Research confidence: 0.85. Verified: True._

### AI Governance Committee Decision Support System
A secure, EU-hosted RAG system that ingests Hermès' internal AI governance policies, regulatory guidelines, and IP protection frameworks. It delivers real-time, cited recommendations to the newly formed AI Governance Committee, flagging IP risks in creative workflows, assessing third-party AI tool compliance, and generating audit-ready summaries of AI usage decisions. The system ensures alignment with Hermès' commitment to human-led creative processes while enabling faster, consistent decision-making on AI adoption.

**Why this company:** Hermès has established a dedicated AI Governance Committee in 2025 to oversee AI use, intellectual property risks, and creative integrity. Current AI usage is limited to IT, supply chain, and internal reporting, with creative processes remaining entirely human-led. This use case directly addresses the committee's need for sovereignty (EU-hosted), multilingual support (French primary), and IP protection—all areas where Mistral's offerings excel.

**Example input:** `Does using an external AI tool to generate marketing copy for our new Evercolor line violate our IP protection framework?`

**Example output:**
```json
{
  "_note": "Illustrative output with synthetic sample data",
  "decision": "Proceed with caution",
  "compliance_status": "Conditional",
  "ip_risk_flag": true,
  "risk_reason": "External tool may retain prompts and
    outputs, risking exposure of proprietary brand voice
    and product details",
  "recommended_action": "Use Mistral's EU-hosted model with
    on-prem deployment to ensure data sovereignty",
  "cited_policies": [
    "Hermès AI Governance Policy v1.2 (Section 3.4:
      External Tool Usage)",
    "EU AI Act Compliance Guidelines (2025)"
  ],
  "audit_trail_id": "GOV-EXAMPLE-2025-001"
}
```

**Blueprint:** `rag` (impact: high · cost: medium · complexity: low · TTV: ~12-16 weeks (estimated))
  _TTV rationale: RAG deployments for governance use cases typically require 12-16 weeks for ingestion, validation, and reviewer UI integration._

**Top risk:** Hallucination in policy citation or misalignment with Hermès' strict IP protection standards

**Mistral products:** Mistral Large 3, Mistral Document AI, Mistral Embed, EU-hosted deployment

**Grounded in:** constraints.data_sovereignty_concerns
_Specificity score: 0.95_

**Architecture blueprint:**
```mermaid
graph LR
  A[User Query] --> B[Retrieve Policies]
  B --> C[Embed & Rank]
  C --> D[Generate Response]
  D --> E[Flag Risks]
  E --> F[Audit Log]
classDef bp_rag fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe,stroke-width:1.5px
class A bp_rag
```

### AI-Powered Authenticity Verification for Resale Market
A vision-language system to verify the authenticity of Hermès products in the secondary market. It analyzes high-resolution images, materials, and craftsmanship details (e.g., stitching patterns, leather textures) against Hermès' proprietary product specifications. The system flags potential counterfeits or inconsistencies, providing confidence scores and detailed explanations for each assessment.

**Why this company:** Hermès' luxury products, such as the Birkin and Kelly bags, are prime targets for counterfeiting, and the resale market is a growing concern. The company's deep proprietary knowledge of its products' unique details (e.g., Veau Epsom, Veau Crispé Togo) makes this a high-impact use case. Mistral's EU sovereignty and on-prem deployment ensure IP protection and data security, aligning with Hermès' governance priorities.

**Example input:** `Is this Birkin bag with Veau Crispé Togo leather and palladium hardware authentic?`

**Example output:**
```json
{
  "_disclaimer": "Synthetic example for demonstration; not
    a factual claim about Hermès.",
  "authenticity_score": "92% (illustrative)",
  "verdict": "Likely Authentic",
  "material_match": {
    "leather": "Veau Crispé Togo (98% match)",
    "hardware": "Palladium (95% match)"
  },
  "craftsmanship_details": {
    "stitching": "Hand-stitched saddle stitch (99% match)",
    "blind_stamp": "Hermès Paris stamp (100% match)",
    "date_code": "Valid format (2023, illustrative)"
  },
  "red_flags": [],
  "recommendation": "Proceed with sale; minor variations in
    hardware finish are within acceptable tolerance."
}
```

**Blueprint:** `fine_tuned_domain` (impact: high · cost: high · complexity: medium · TTV: ~16-24 weeks (estimated))
  _TTV rationale: Fine-tuning vision-language models for niche luxury verification requires extensive data curation and validation._

**Top risk:** False positives/negatives in authenticity assessment, damaging brand trust or enabling counterfeit sales

**Mistral products:** Mistral Large 3, Pixtral (vision-language understanding), Mistral fine-tuning, On-prem deployment

**Grounded in:** business.key_products_or_services[4], business.key_products_or_services[5], business.key_products_or_services[6]
_Specificity score: 0.90_

**Architecture blueprint:**
```mermaid
graph LR
  A[Upload Image] --> B[Vision-Language Model]
  B --> C[Extract Features]
  C --> D[Compare to DB]
  D --> E[Generate Report]
  E --> F[Flag Anomalies]
classDef bp_fine_tuned_domain fill:#581c87,stroke:#a855f7,color:#f3e8ff,stroke-width:1.5px
class A bp_fine_tuned_domain
```

### Artisan Knowledge Capture and Multilingual Training System
A secure, on-prem system to digitize and structure Hermès' artisanal craftsmanship knowledge (e.g., leatherworking techniques for Birkin/Kelly bags) into a searchable, multilingual knowledge base. Artisans can query in French or English to retrieve step-by-step guides, material specifications, or historical techniques. The system generates summaries, visual aids, and cross-referenced best practices while restricting access to authorized personnel to protect IP.

**Why this company:** Hermès' core differentiation lies in its artisanal craftsmanship, particularly for iconic products like the Birkin and Kelly bags. While creative processes must remain human-led, digitizing and preserving this knowledge ensures continuity, scalability, and faster onboarding. The need for multilingual support (French/English) and strict IP protection aligns with Mistral's EU sovereignty, fine-tuning, and on-prem deployment capabilities.

**Example input:** `Comment réparer une couture sur un sac Kelly en Veau Epsom sans endommager le cuir?`

**Example output:**
```json
{
  "_note": "Illustrative output with synthetic sample data",
  "query_language": "French",
  "response_language": "French",
  "summary": "Pour réparer une couture sur un sac Kelly en
    Veau Epsom, utilisez un fil de soie ciré de couleur
    assortie et une aiguille fine. Travaillez de
    l'intérieur vers l'extérieur pour éviter les traces
    visibles. Utilisez le point de selle traditionnel
    Hermès.",
  "step_by_step": [
    {
      "step": 1,
      "description": "Nettoyer la zone avec un chiffon
        humide et sec (produit: CASE-EXAMPLE-001)."
    },
    {
      "step": 2,
      "description": "Aligner les bords de la couture avec
        une pince fine."
    },
    {
      "step": 3,
      "description": "Coudre avec le point de selle, en
        maintenant une tension uniforme."
    }
  ],
  "material_notes": "Veau Epsom est plus résistant que Veau
    Swift; utiliser une pression modérée.",
  "related_techniques": [
    "Réparation des anses en cuir",
    "Entretien des fermetures éclair"
  ],
  "ip_protection": "Accès restreint aux artisans certifiés
    Hermès."
}
```

**Blueprint:** `document_ai_pipeline` (impact: medium · cost: high · complexity: medium · TTV: ~20-30 weeks (estimated))
  _TTV rationale: Digitizing tacit artisan knowledge and building a secure, multilingual system requires extensive collaboration with subject-matter experts._

**Top risk:** Inadvertent exposure of proprietary craftsmanship techniques through system queries or outputs

**Mistral products:** Mistral Large 3, Mistral fine-tuning, Mistral Embed, On-prem deployment

**Grounded in:** business.key_products_or_services[6], business.key_products_or_services[7]
_Specificity score: 0.85_

**Architecture blueprint:**
```mermaid
graph LR
  A[Artisan Query] --> B[Retrieve Knowledge]
  B --> C[Multilingual Embed]
  C --> D[Generate Response]
  D --> E[IP Access Control]
  E --> F[Audit Trail]
classDef bp_document_ai_pipeline fill:#064e3b,stroke:#10b981,color:#d1fae5,stroke-width:1.5px
class A bp_document_ai_pipeline
```

## Considered but not selected
- **Supply Chain Intellectual Property Risk Monitoring** — Lower strategic fit; Hermès' current AI usage is limited to IT and supply chain, but governance and brand protection are higher priorities.
- **Sustainable Material Sourcing and Traceability Agent** — Lacks immediate grounding in Hermès' stated priorities; authenticity and governance are more urgent.
- **Multilingual Customer Insight Synthesis for Global Retail** — Hermès' creative processes remain human-led; customer insights are less aligned with current AI governance focus.
- **Personalized Luxury Product Recommendation Agent** — Misaligned with Hermès' emphasis on human-led creative processes and IP protection over sales automation.

---
## Report quality signals

- **Topical diversity** (LLM-graded over titles + blueprint patterns): `0.95`
- **Specificity** per use case: `0.95`, `0.90`, `0.85`
- **Mistral product diversity**: `7` distinct products across the three use cases
- **Time-to-value spread**: 12–30 weeks (across 3 use cases)
- **Cost-tier spread**: medium, high, high
- **Fact-check pass rate**: `93%` (13/14 claims supported by research)

### Fact-check detail (per claim)

**Unsupported (1):**
- [hermes-authenticity-verification] Hermès' luxury products, such as the Birkin and Kelly bags, are prime targets for counterfeiting `[judge: rejected]` — _The snippet only lists Hermès bag models without addressing counterfeiting. (was: Corroborated via web search: Let us know in the comments @miaplainer #hermes #birkinbag #birkin #luxury ... bags exist l)_

**Supported (13):** — **2 rescued via web search (1 verified, 1 corroborated)**
- [hermes-governance-ai-committee-assistant] Hermès has established a dedicated AI Governance Committee in 2025 — The maison just announced it is establishing a dedicated "Artificial Intelligence Governance Committee" in 2025.
- [hermes-governance-ai-committee-assistant] The AI Governance Committee oversees AI use, intellectual property risks, and creative integrity — Its role? To oversee how AI is used across the company, including risks to intellectual property, creative integrity, and long-term brand va…
- [hermes-governance-ai-committee-assistant] Hermès' current use of AI is limited to IT, supply chain, and internal reporting via external platforms — Hermès clarified that its current use of AI is limited, focused on IT, supply chain, and internal reporting via external platforms.
- [hermes-governance-ai-committee-assistant] Creative and artisanal processes at Hermès will remain entirely human-led — Creative and artisanal processes will remain entirely human-led.
- [hermes-authenticity-verification] The resale market for Hermès products is a growing concern [`verified ↗`](https://www.businessinsider.com/hermes-ceo-new-birkins-resale-market-bad-mood-2025-7) — Rescued via web search (verified source): # Hermès' CEO says seeing new Birkins on the resale market puts him in a bad mood. * "I pull a fac…
- [hermes-authenticity-verification] Hermès has deep proprietary knowledge of its products' unique details (e.g., Veau Epsom, Veau Crispé Togo) — Hermès has a large assortment of materials for their handbags, shoes, belts, and small leather goods. Each leather is selected and treated w…
- [hermes-authenticity-verification] Veau Epsom is a Hermès leather type — Hermès has a large assortment of materials for their handbags, shoes, belts, and small leather goods.
- [hermes-authenticity-verification] Veau Crispé Togo is a Hermès leather type — Commonly made of various leathers, including Togo, Clemence and Epsom and even exotic skins, such as Porosus Crocodile.
- [hermes-artisan-knowledge-preservation] Hermès' core differentiation lies in its artisanal craftsmanship, particularly for iconic products like the Birkin and Kelly bags — In many circles, perhaps the most famous leather product in the world is the Hermès Birkin Bag, a universal emblem of luxury, exclusivity an…
- [hermes-artisan-knowledge-preservation] Hermès has sixteen métiers — HERMÈS, ONE HOUSE, SIXTEEN MÉTIERS Leather goods-saddlery, women’s silk, men’s silk, women’s ready-to-wear, men’s ready-to-wear, shoes, belt…
- [hermes-artisan-knowledge-preservation] Hermès' creative processes must remain human-led — Creative and artisanal processes will remain entirely human-led.
- [hermes-artisan-knowledge-preservation] Hermès has a need for multilingual support (French/English) [`corroborated ↗`](https://www.welcometothejungle.com/en/companies/hermes/jobs/cdd-trilingual-client-advisor-german-english-french_paris_HERMS_NGQ2Azo) — Corroborated via web search: Hermès is looking for a trilingual customer advisor, fluent in German, English and French, for its European e-c…
- [hermes-artisan-knowledge-preservation] Hermès has a need for strict IP protection — Its role? To oversee how AI is used across the company, including risks to intellectual property, creative integrity, and long-term brand va…


**Meta-evaluator confidence**: `0.78` (NOT ready — needs revision)
**Cross-cutting concern**: All three use cases rely heavily on the company context's claim about Hermès' AI Governance Committee, but this claim is only supported by a single LinkedIn post (ev-c0a3c2edfb) with no corroborating official Hermès sources. The committee's existence, scope, and priorities are critical to all proposals but are under-documented.