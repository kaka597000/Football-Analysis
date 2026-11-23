"""First-stage research goals for original and newly registered cohorts."""

ORIGINAL_SCHEMA = 'WCPP-KPI-REGISTRATION-1'
CURRENT_SCHEMA = 'WCPP-KPI-REGISTRATION-2'
ORIGINAL_TARGETS = {'score_top1': .20, 'score_top3': .40, 'wdl': .65, 'actual_ou': .70}
TARGETS = {**ORIGINAL_TARGETS, 'wdl': .60}
PROTOCOLS = {ORIGINAL_SCHEMA: ORIGINAL_TARGETS, CURRENT_SCHEMA: TARGETS}
