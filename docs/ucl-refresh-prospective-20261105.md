# UCL fixed-refresh: actual first future pairs

The inactive original-recipe refits are now paired with the existing Current/Test on genuinely future fixtures. This remains statistical research; no active registry was switched and no future outcome is claimed.

## Executed population

The artifact/source protocol was locked at 2026-11-05 08:51 Beijing before the source acquisition. A separate actual ESPN acquisition retained the entire first two upcoming league matchdays: 36 fixtures kicking off on Beijing November 25–26 and December 9–10 (the retained source index also preserves the original timezone-aware timestamps). All source events were kept; all 36 passed event/team/kickoff, league-phase, scheduled-status and explicit-neutral checks.

The full population was preregistered before inference. Each target then used one actual new cutoff and identical input for the active pair and inactive refit pair. All 36 first pairs were captured, with four full probability matrices per match (144 matrices), source/fixture/artifact checksums, and separate `current-test-pair` / `ucl-refresh-current-test` frozen tracks.

- Registered: 36.
- First paired: 36; missing: 0.
- Completed and settled: 0; pending: 36.
- Matrices differ in all 36 Current comparisons and all 36 Test comparisons.
- Statistical Primary differs in 18 Current and 21 Test comparisons. These are prediction changes, not extra hits or evidence of an accuracy gain.

The existing domestic dynamic-Poisson experiment is a separate population and cannot validate these UCL refits. At the local pre-deployment checkpoint its 78 new registered targets had 77 actual pairs and one target reserved for the portable automation path; its older two EPL pairs remain separate. The protocol minimum applies per cohort, not by pooling leagues.

## Strict result review

`runtime/research_program/ucl_refresh_review.py` reviews two comparisons independently: active Current versus refreshed Current, and active Test versus refreshed Test. It verifies the original preregistration, first frozen tracks, matching input/cutoff, locked artifact hashes, matrix hashes and actual pre-kickoff generation times. It accepts only retained ESPN full-time regulation results with exact source match/team/kickoff reconciliation, query time, SHA-256, and two-period/two-half checks. Live, missing and unpaired observations stay explicitly unscored.

Primary, WDL and real quoted-line totals keep independent denominators. Quarter lines retain all five settlement categories. Probability, tail and Primary-confidence diagnostics are separate from the highest-priority Primary net gain. No reference records are synthesized or backdated to fit another review adapter.

The review implementation was separately hash-locked after capture began but before any outcome; its lock explicitly records that chronology rather than pretending it preceded already-issued predictions. A real empty-result review has executed against all 36 actual pairs. Both comparisons retain the 30-completed threshold, full-population requirement, and independent source/market audit blockers. No software result automatically activates a model.

Example (all paths are retained artifacts, not test data):

```bash
PYTHONPATH=runtime python -m research_program.ucl_refresh_review \
  --protocol /absolute/ucl-refresh-protocol.json \
  --plan /absolute/actual-plan-freeze.json \
  --packages /absolute/ucl-paired-packages.json \
  --results /absolute/retained-result-observations.json \
  --evidence-root /absolute/result-source-root \
  --out /absolute/new-review.json
```

Actual artifacts: `/workspace/WCPP/research/nine-items-20261105/prospective/`, including `ucl-future-data`, `ucl-refresh-plan-receipt.json`, `ucl-paired-packages.json`, `ucl-refresh-real-review.json`, and `ucl-review-implementation-lock.json`. Remote automation support must be verified separately; local capture alone does not prove remote deployment.

Validation: the forward/selection tests passed (9 checks), and the separate review suite passed 4 checks covering pending/missing population, retained-source corruption, four independent metrics, duplicate rejection, and actual quarter-line half-loss settlement. Synthetic fixtures stay in temporary test directories and never enter the real forecast ledger.
