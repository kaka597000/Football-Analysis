# Portable first-prematch capture and result review

`runtime/automation/prospective_cycle.py` adds a private-state execution wrapper around the existing locked validators. The wrapper does not change `future_validation.py`, `ucl_refresh_forward.py`, an active registry, model weights, old forecast bytes, or promotion rules.

## Two independent populations

The domestic bundle (`export` / `cycle`) retains the original seven-cohort dynamic-Poisson protocol. The combined actual bundle has 80 preregistered fixtures: 79 have real first-prematch Current/Test plus independent candidate pairs, and one unissued fixture is reserved for scheduled capture. Its initial portable review was executed with zero acquisition calls: 79 pending, zero completed, one missing pair. The original two targets and their records remain in the combined review. The sample gate is per cohort; 80 across different leagues does not satisfy 30 completed fixtures in any one league.

The UCL bundle (`export-ucl` / `review-ucl`) retains a separate locked comparison of old Current/Test against both fixed-recipe refreshed candidates. The actual review includes 36 preregistered and first-paired unstarted fixtures, zero missing pairs, and zero completed results. It is review-only: this wrapper never generates a new UCL prediction or changes the already frozen pairs. Both old-versus-refreshed comparisons keep independent denominators and the original minimum-completed gate.

These are different protocols and cannot be pooled to claim that a UCL refresh or a domestic candidate has passed validation. All current fixtures are genuinely future observations; no future results are supplied, inferred, or counted.

## Integrity and recovery

Bundles store original object bytes by SHA-256 and preserve receipt metadata separately. Hydration checks the running inference/review sources against the locked hashes. Existing conflicting files are fatal; original protocol and forecast records are never rewritten to make relocation pass.

A cycle locks its private state. It checkpoints each registered cohort, successful pair, and certified result. If a process stopped between a durable plan or pair and its state checkpoint, a later cycle recovers only matching original identities and hashes. An already issued pair may be recovered after kickoff without inference; a genuinely missing late forecast remains missing. Source outages are recorded independently so an earlier successful pair is retained. New capture uses a fresh exact-event ESPN summary to confirm teams, kickoff, unstarted status and explicit neutral flag. Stale or absent actual-line quotes do not become fabricated markets.

Result acquisition is capped by requests and elapsed time. Only exact-event full-time regulation labels with retained raw-response checksum, team identity and observation time are accepted through the existing result validator. Pending and missing records remain in the registered population. No automatic promotion is implemented.

## Existing private workflow integration

The private workflow can call both bundles after checking out `future/`, installing the CPU numerical dependencies, and preparing the allowed original `/workspace` paths. It must keep the existing schedules and shared concurrency, preserve the five-minute job limit, and bound the domestic and UCL stages separately. A successful local export/review is not evidence of a deployed remote job; remote run and persisted-state evidence must be reported separately.

The accompanying private K League fallback accepts only the verified FotMob K League 1 catalogue identity (9080, KOR, men, selected season). Unknown phase and venue remain candidate-only; no rows are silently appended to training. Primary provider permission/key errors remain in the audit and CLI status. Public candidate collection does not mean the repository API secret has been repaired.

## Checks and actual execution

Fourteen wrapper tests passed, covering object/source tampering, destination conflicts, full-population registration, first-slot protection, orphan-plan recovery, post-kickoff pair recovery without inference, source outage checkpointing, UCL schema separation, pending results and wrong-event result rejection. Eleven private Korean-collector tests passed, including the candidate-only fallback and retained authentication failure. The two real portable reviews are saved under `nine-items-20261105/automation/` in the private runtime assets.
