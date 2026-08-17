# Scoring Model

Version: `mvp-1`

The engine uses deterministic rules. Prospect value and data quality are separate. A record with no verified Strength 3-or-better organizational assertion is not scored.

## Prospect score: 0-100

| Component | Maximum | Current rules |
|---|---:|---|
| Workforce relevance | 25 | Union/labor organization: 13; priority workforce industry: 8; stable relevant-workforce geography: up to 4; presence of OSHA establishment context: 2. Total capped at 25. Injury counts and rates add zero points. |
| Estimated organizational reach | 20 | 5,000+: 20; 2,000+: 17; 750+: 14; 250+: 10; 75+: 6; documented positive reach: 3. Unknown is zero, never fabricated. |
| Public organizational role influence | 20 | Current verified business manager/agent/representative: 20; president/principal officer/secretary-treasurer: 18; membership/organizing: 16; safety/training: 16; apprenticeship: 15; event coordinator or public manager: 12; steward: 10. Only current sourced roles count. |
| Proximity | 15 | Tier A: 15; B: 12; C: 8; D: 4; outside/unknown: 0. The MVP labels mileage as an estimate, not a routed result. |
| Public accessibility/current events | 10 | Verified public professional channel: 3; public-facing access: 3; upcoming events: up to 3; active program: 1. Permission-required events remain marked as such. |
| Long-term relationship potential | 10 | Union/labor: 8; association/chamber/trade: 7; community/nonprofit/apprenticeship: 6; workplace: 4; other organization: 3; active program adds 2, capped at 10. |

Role influence concerns authority in a public organizational capacity. Personal popularity, follower counts, wealth, inferred relationships, and protected traits are not inputs.

## Data-quality score: 0-1

The dashboard labels this value **Criteria fulfilled** so reviewers do not have to interpret a decimal. The display scale is stable rather than relative to the current dataset: `Not much` is below 0.25, `A little` is 0.25-0.49, `Some` is 0.50-0.74, `Most` is 0.75-0.89, and `Many` is 0.90-1.00. The underlying deterministic score and formula remain unchanged.

```text
data_quality =
    0.30 * average_eligible_source_strength_normalized
  + 0.25 * material_field_source_coverage
  + 0.20 * current_assertion_share
  + 0.15 * entity_identity_confidence
  + 0.10 * conflict_penalty
```

The conflict term is 1 with no conflicts and decreases to 0 at three or more conflict groups. Data quality never increases the raw prospect score above its raw value.

```text
adjusted_priority = raw_prospect_score * (0.70 + 0.30 * data_quality_score)
```

## Entity-match confidence

| Confidence | Treatment |
|---|---|
| 0.95-1.00 | Exact authoritative identifier or exact name/address evidence; automatic merge allowed |
| 0.80-0.94 | Strong multi-field evidence; automatic merge allowed only when locality or professional identifier corroborates |
| 0.60-0.79 | Probable match; records remain separate and are flagged for review |
| Below 0.60 | Separate records |

Name similarity alone never triggers an automatic merge. Conflicting facts are stored as competing assertions and the canonical value is not silently overwritten.

## Explanations

Each score produces a plain-language explanation based on the factors that actually added points. The generator does not claim an employer is unsafe, predict that anyone will be injured, or state that workers are likely clients.

## Aggregate ACS handling

Age is stored only on Census geographies. The engine reports aggregate population age 40+ and aggregate employed population age 45+ because the selected ACS employment table does not provide a defensible 40-44 worker split. It does not interpolate the missing ages or transfer a community estimate to a named person.

Margins of error are combined by square root of the sum of squares for summed estimates. Geography stability is `stable`, `caution`, or `unstable`; unstable relevant-workforce estimates do not contribute points.

## Future learning

Outcome labels may later include research completed, approved outreach, productive conversation, meeting, presentation accepted, card placement approved, referral, and aggregate signed-matter outcome. No model should be trained until the label set is meaningful. Any later logistic-regression or lightweight boosted-tree model must use time-based validation, beat this baseline, and exclude protected traits, individual age, health information, injury narratives, and confidential client data.
