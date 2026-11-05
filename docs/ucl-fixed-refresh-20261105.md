# UCL fixed-recipe freshness execution

The active UCL Current/Test artifacts stop at 2026-09-10. An isolated result-only refresh has now actually fit new, inactive artifacts through 2026-11-04, preserving the Current baseline recipe and expert weights, and the Test UCL LightGBM configuration, feature list and original alpha=1.0. No active registry, engine, old artifact or issued forecast was changed.

`runtime/research/ucl_fixed_refresh.py` records its protocol and input identity before fitting. Additions require a retained ESPN summary hash, exact event/team/competition/phase, the 2024-onwards league-phase format, two regulation-period scores, explicit nonneutral status, and an actual observation/audit time. A source capture time is never reclassified as historical first-known availability. Unknown original fetch times are represented as null; retained-file re-audit times are labeled separately. New xG/process channels are refused rather than zero-filled. Existing history dictionaries and existing genuine process fields remain unchanged.

## Actual inputs and artifacts

- Source inventory: 60 captured full-time UCL summaries for the provider's 2026-09-10–2026-11-04 window. Six overlap the active history; 54 are new. New captures reconcile with retained overlapping files, zero score/identity conflicts.
- Final history: 306→360, training end 2026-09-10→2026-11-04. New additions are result-only; the Test artifact still has 306 historical process rows and zero xG rows. This is not a claim that new xG, injuries or lineups were learned.
- Final private execution: `/workspace/WCPP/research/nine-items-20261105/freshness/run-final-002/`. The earlier `run-retained-001` is an initial retained-source engineering run, not a selected alternative or final result.
- Current artifact SHA256: `07079b847869db39ad42eafc2bd127200b672aadcb9b18d1ed347b68dea55999`.
- Test artifact SHA256: `8e6b8816ae714f36f666e08e95c18d4864159e3d940dad59735e95be5530c0fa`.
- Both artifacts keep their strict loader engine versions. Their distinct hash and `refresh_experiment.id=UCL-FIXED-REFRESH-20261105` identify the inactive refresh. Old acceptance metadata is explicitly marked as not applying to the refit. No future accuracy or promotion is claimed.

## Date-isolated reconstruction

For every target month after the old training cutoff, the refreshed models use only rows dated before day one of that month. Recipes, alpha and features are fixed before scoring. Frozen originals are the stale reference. All 54 target records retain four complete matrices, fold identities and file/record hashes; cold-start refusals would remain in coverage rather than become a silent fallback. First-known history is absent, so this is post-match result-date reconstruction, not actual historical as-of replay or prospective validation.

| Fixed lane | Top1 old→refresh | WDL old→refresh | Score log-loss old→refresh | WDL RPS old→refresh |
|---|---:|---:|---:|---:|
|Current|1→1 /54|32→33 /54|3.6401→3.6076|0.21314→0.21606|
|Test|2→2 /54|35→35 /54|3.5588→3.5691|0.20413→0.21010|

Neither lane has any net Top1 gain. Probability constraints do not improve consistently. These results do not support promotion. No verified historical market joins were used; the run contains no real-line OU performance claim.

A separate reload check compares the original artifact's reconstructed matrices for the nine actual Nov4 first-prematch freezes with those immutable frozen matrices: **18/18 hashes match exactly**. This verifies reference loading; it does not make refreshed post-match outputs into old forecasts.

## Checks and future use

Ten targeted tests pass: fixed Current weights/recipe, unchanged Test alpha/features, old-object immutability, retained-file audit versus actual fetch distinction, source-event URL matching, response tamper detection, explicit neutral, two-period score boundaries, network observation ordering, pre-new-format exclusion and refusal of unverified first-known/xG fields. Numerical execution additionally verifies active registry/artifact hashes unchanged and serializes the actual fitted candidates.

A separate genuinely future protocol must lock the final hashes before choosing/forecasting targets. It should compare both old and refreshed pairs under the same real cutoff and retain pending/refused records. Artifact availability begins at the actual 2026-11-05 build time, never a reconstructed historical cutoff. Existing activity should continue until sufficient untouched future evidence passes the Primary, coverage, calibration and tail-quality constraints.
