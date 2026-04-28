# ICP Scoring — H&V Sales Intelligence

Each lead is scored **0–100** across eight ICP-aligned dimensions, derived from the H&V Ideal Customer Profile document. The scorer is **deterministic** (same inputs always produce the same score) and **transparent** — every lead carries a per-dimension breakdown so SDRs see exactly *why* a lead scored what it did.

| # | Dimension | Max | What it measures | Source |
|---|---|---:|---|---|
| 1 | **Geography** | 10 | Florida = focus market (10), other US = operating geography (5), outside US = 0 | Google Places address |
| 2 | **Specialty fit** | 15 | Core ICP specialties (psychiatry, primary care) = 15; parallel (dental, chiropractic) = 10; adjacent (urgent care) = 5; other = 3 | Google Places category + name |
| 3 | **Practice size** | 15 | Cat A (~1–3 providers, <50 reviews) = 15; Cat B (~3–5 providers, <150) = 12; Cat C (~5–10 providers, opportunistic) = 5; Cat D (~10+, expansion) = 10 | Review count proxy |
| 4 | **Rating opportunity** ⚡ | 20 | **Inverse** — lower stars = higher score. <3.0★ = 20; 3.0–3.5★ = 17; 3.5–4.0★ = 13; 4.0–4.3★ = 9; 4.3–4.5★ = 5; 4.5+★ = 2 | Google rating |
| 5 | **Review depth** | 10 | Data-quality signal — 100+ reviews = 10, 50+ = 8, 20+ = 6, 5+ = 4, <5 = 1 | Google review count |
| 6 | **Website presence** | 5 | Has a website = 5, no website = 0 | Google Places |
| 7 | **Hiring signals** | 15 | Open roles, careers page, "we're hiring" pages — scaled 0–15 from a 0–100 AI score | OpenAI analysis of website + reviews |
| 8 | **Urgency** | 10 | Wait-time complaints, understaffing references, recent negative reviews — scaled 0–10 from a 0–100 AI score | OpenAI analysis of website + reviews |
| | **Total** | **100** | | |

## Score interpretation

| Range | Meaning |
|---|---|
| **80–100** | Ideal lead — Florida core specialty, struggling, small/medium, hiring signals present |
| **50–79** | Solid prospect — US, core or parallel specialty, some pain signals |
| **30–49** | Stretch — out-of-state, adjacent specialty, or already thriving |
| **<30** | Poor fit — outside ICP |

## Design rationale

- **Rating is inverted** because H&V's value proposition is *fixing* operational pain. A 5★ practice has no problem to solve; a 3★ practice with bad reviews about "long wait times" and "rude front desk" is exactly the staffing problem we sell into.
- **Practice size uses review count as a proxy.** Google Places doesn't expose provider count, but in our sample, review count correlates well with practice size. The thresholds (50 / 150 / 400) map roughly to the four ICP categories.
- **Geography is heavily weighted toward Florida** to reinforce the initial-market focus called out in the ICP doc. Outside-US leads score 0 here, capping their max total at 90 — making it nearly impossible for an off-geography lead to outrank an in-geography one of comparable quality.
- **AI signals (hiring + urgency) are bounded at 25 combined points.** They're powerful but noisier than the structural signals, so they can't single-handedly push a poor-geo / poor-specialty lead into the top tier.
- **Specialty fit caps at 15.** Core specialties get the full 15; adjacent ones still get partial credit so they don't get filtered out of the SDR's view, just down-ranked.

## Transparency

Every scored lead persists its full breakdown — `{label, score, max, reason}` per dimension — in `practices.icp_breakdown` (JSONB). The UI shows this on each lead card so an SDR can:

- Sort by score and trust the ranking
- Click any lead and see *why* it scored what it did
- Spot misclassifications immediately (e.g., a Florida psychiatry lead showing only 5 pts in Geography means something's wrong with the address parse)

## How it's computed

1. Search returns practices from Google Places.
2. Out-of-domain results (cafés, restaurants, etc.) are dropped via a hard filter; healthcare practices proceed.
3. **Analyze** runs OpenAI over the website + Google reviews + third-party review sites. Returns urgency + hiring signal scores.
4. The deterministic ICP scorer combines those AI signals with the structural data (geography, specialty, size, rating, reviews, website) to produce the 0–100 total.
5. Score + breakdown are written to the DB.

## Iteration plan

The weights are config — easy to tune as we learn. After the first quarter of pipeline data, we can correlate *closed-won* outcomes against ICP scores, identify which dimensions actually predict revenue, and rebalance accordingly.

## Implementation references

- Scorer: [src/icp_scorer.py](../src/icp_scorer.py)
- Tests: [tests/test_icp_scorer.py](../tests/test_icp_scorer.py)
- Source ICP document: internal — see H&V ICP brief
