# ICP Scoring — Apex Universal ICP

Every lead is scored **0–100** against Apex's [Universal ICP Qualification Model](../ICP%20Documents/). The scorer assigns each account to one of five **verticals** and one of four **tiers**, then evaluates it across **seven dimensions**.

## The five verticals

| Vertical | Description |
|---|---|
| `medical` | Psychiatry, primary care, internal medicine, family medicine; also parallel clinical (chiropractic, urgent care, specialty) |
| `dental` | General + specialty dental (ortho, perio, endo, oral surgery, pediatric) |
| `alf_nh` | Assisted living facilities, memory care, nursing homes, senior living groups |
| `hotel_resort` | Hotels, resorts, vacation rental managers, boutique properties |
| `medspa_wellness` | MedSpas, day spas, wellness/coaching studios, physician-led aesthetics clinics, resort spas |

## The four tiers

| Tier | Profile | Motion |
|---|---|---|
| **A** | Small / single-location / owner-led | Single-seat entry — primary |
| **B** | Growth-stage / mid-sized | Highest fit; single seat → function expansion |
| **C** | Mid-market / specialty / multi-property | Selective or opportunistic |
| **D** | Enterprise / corporate / multi-state | Opportunistic only; longer procurement cycle |

The vertical and tier are inferred by the analyzer from the website + reviews + Google Places metadata.

## The seven dimensions (100 pts total)

| # | Dimension | Max | What it measures |
|---|---|---:|---|
| 1 | **Vertical fit** | 15 | Vertical × tier × geography (Florida = 100%, other US = 60%, outside US = 0). Deterministic. |
| 2 | **Operational pain** | 20 | Clear admin/scheduling/documentation/communication/billing/follow-up burden. AI-inferred from reviews + website. |
| 3 | **Decision-maker access** | 15 | Is there an identifiable owner / GM / administrator / practice manager who can approve recurring spend? AI-inferred. |
| 4 | **Remote readiness** | 15 | Uses digital systems we can plug into — EHR/PMS/CRM (Dentrix, Open Dental, PointClickCare, Opera, Aesthetic Record, etc.), online booking, patient portals. AI-inferred. |
| 5 | **Role clarity** | 15 | Can a narrow remote role be defined today? Specific job postings, named functions, clearly non-clinical scope. AI-inferred. |
| 6 | **Budget maturity** | 10 | Can they support a recurring monthly seat cost (not a one-time project)? Practice size, software stack, multi-location, market positioning. AI-inferred. |
| 7 | **Compliance boundary** | 10 | Does the engagement stay within Apex's non-clinical, non-physical scope? AI-inferred — lower if they appear to expect remote staff to do licensed clinical work or in-person tasks. |

## Score interpretation

| Range | Classification | Action |
|---|---|---|
| **85–100** | Strong ICP | Advance to demo / role definition |
| **70–84** | Qualified with conditions | Advance if role scope is narrow |
| **55–69** | Weak / exploratory | Nurture or defer |
| **<55** | Poor fit | Disqualify |

## Vertical fit — deterministic logic

Tier base scores (in Florida) are:

| Tier | Base / 15 |
|---|---:|
| A | 13 |
| B | 15 |
| C | 9 |
| D | 6 |

Outside Florida (but inside the US), the base is scaled to **60%** and rounded — so a Tier B target in Texas scores **9** on Vertical fit instead of 15. Targets outside the US score **0** regardless of vertical or tier.

This is the only dimension whose ceiling is structural — the other six are AI-inferred from the website + reviews.

## Design rationale

- **Florida focus is enforced by the geography multiplier.** Florida is Apex's launch market per the ICP doc. Outside-US leads cap at 85 and outside-FL US leads cap at 94 — making it nearly impossible for an off-geography lead to outrank an in-geography one of comparable quality.
- **Six of seven dimensions are AI-inferred** because they're interpretive — pain, decision-maker access, remote readiness, role clarity, budget maturity, and compliance boundary all require reading the website and reviews. Pure structural data (Google Places category, review count) can't tell you whether the owner is engaged or whether they use Dentrix.
- **AI signals are bounded at 85 combined points.** They're powerful but noisier than the structural Vertical fit signal. A target in a wrong geography or a non-ICP vertical can't be carried into the top tier by AI scores alone.
- **Tier-aware fit.** Tier B (growth stage) scores higher than Tier A (single location) because the PDFs flag B as "Very High" fit across every vertical — those accounts can support multi-seat expansion.

## Transparency

Every scored lead persists its full breakdown — `{label, score, max, reason}` per dimension — in `practices.icp_breakdown` (JSONB). The classified vertical and tier live in `practices.icp_vertical` and `practices.icp_tier`. The UI shows the breakdown on each lead card so an SDR can:

- Sort by score and trust the ranking
- See *why* a lead scored what it did, dimension by dimension
- Spot misclassifications immediately (e.g., a Florida dental practice showing only 9 pts in Vertical fit means the analyzer either misclassified the vertical or the address parsed wrong)

## How it's computed

1. Search returns practices from Google Places.
2. Out-of-domain results (cafés, restaurants, etc.) are dropped via a hard filter; in-vertical accounts proceed.
3. **Analyze** runs OpenAI (default `gpt-4.1`) over the website + Google reviews + third-party review sites. The analyzer returns:
   - Classified `icp_vertical` (one of the five verticals or `null` for `other`)
   - Classified `icp_tier` (A / B / C / D)
   - Six AI dimension scores (0–100 each)
   - `summary`, `pain_points`, `sales_angles` for the SDR
4. The deterministic ICP scorer combines vertical + tier + state to compute Vertical fit, and scales the six AI scores into the 100-point total.
5. Score + breakdown + vertical + tier are written to the DB.

## Iteration plan

The weights are config — easy to tune as we learn. After the first quarter of pipeline data, we can correlate *closed-won* outcomes against ICP scores, identify which dimensions actually predict revenue, and rebalance accordingly. The Florida multiplier and tier base scores are also adjustable as we expand beyond the initial market.

## Implementation references

- Scorer: [src/icp_scorer.py](../src/icp_scorer.py)
- Analyzer (AI prompt + scoring): [src/analyzer.py](../src/analyzer.py)
- Tests: [tests/test_icp_scorer.py](../tests/test_icp_scorer.py)
- Source ICP documents: [ICP Documents/](../ICP%20Documents/) — five PDF segment briefs + universal qualification model
