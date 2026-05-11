# v9.8 — Upfront entity resolution validation

Tests that upfront entity resolution (one Mistral Small call at the top of
the pipeline) correctly: (a) rewrites ambiguous short inputs to canonical
names, (b) refuses gibberish/empty/unidentifiable inputs in ~2s, (c) doesn't
regress real-company runs.

| Input | Tier | Wall (s) | Resolved → | Conf | Pass | SE-ready | Status |
|---|---|---:|---|---:|---:|:-:|---|
| Apple | standard | 175.2 | Apple Inc. | 0.35 | 50% | ✗ | ok |
| Microsoft | standard | 189.9 | Microsoft | 0.65 | 80% | ✗ | ok |
| Hermes | standard | 169.8 | Hermès International S.A. | 0.79 | 94% | ✗ | ok |
| Carrefour | standard | 182.3 | Carrefour Group S.A. | 0.68 | 83% | ✗ | ok |
| Joe's Pizza Shop | standard | 190.9 | Joe's Pizza | 0.68 | 73% | ✗ | ok |
| asdfqwerty | standard | 0.6 | — | — | — | — | REFUSED — Couldn't identify a specific company for 'asdfqwerty'. Random keystrokes — no id |
| ZYX Corporation | standard | 2.0 | — | — | — | — | REFUSED — Couldn't identify a specific company for 'ZYX Corporation'. No single well-known |
| Apple | max | 213.1 | Apple Inc. | 0.31 | 46% | ✗ | ok |