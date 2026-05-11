# v9.7 — hardened generate prompt + meta-eval UX fix + max insane mode

**Standard runs (3× per company)** — averages variance to compare model variants honestly.
**Max run** — tests the insane-mode polish-on-Large + critique-revise pass.

| Company | Tier | Run | Wall (s) | Conf | Pass | SE-ready | Status |
|---|---|---:|---:|---:|---:|:---:|---|
| L'Oréal | standard | 1 | 184.7 | 0.78 | 79% | ✗ | ok |
| L'Oréal | standard | 2 | 155.8 | 0.69 | 73% | ✗ | ok |
| L'Oréal | standard | 3 | 152.7 | 0.88 | 88% | ✓ | ok |
| BNP Paribas | standard | 1 | 156.8 | 0.85 | 100% | ✗ | ok |
| BNP Paribas | standard | 2 | 164.2 | 0.75 | 80% | ✗ | ok |
| BNP Paribas | standard | 3 | 172.7 | 0.68 | 78% | ✗ | ok |
| Carrefour | standard | 1 | 173.8 | 0.54 | 68% | ✗ | ok |
| Carrefour | standard | 2 | 166.4 | 0.61 | 76% | ✗ | ok |
| Carrefour | standard | 3 | 180.9 | 0.85 | 100% | ✗ | ok |
| Hermes | max | 1 | 192.8 | 0.63 | 78% | ✗ | ok |

## Per-company averages (excluding errors / refusals)

| Group | n | Mean wall | Mean conf | Mean pass | SE-ready count |
|---|---:|---:|---:|---:|---:|
| L'Oréal / standard | 3 | 164.4s | 0.79 | 80% | 1/3 |
| BNP Paribas / standard | 3 | 164.6s | 0.76 | 86% | 0/3 |
| Carrefour / standard | 3 | 173.7s | 0.67 | 81% | 0/3 |
| Hermes / max | 1 | 192.8s | 0.63 | 78% | 0/1 |