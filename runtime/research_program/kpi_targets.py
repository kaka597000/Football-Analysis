"""First-stage research goals for original and newly registered cohorts."""

ORIGINAL_SCHEMA = 'WCPP-KPI-REGISTRATION-1'
ORIGINAL_TARGETS = {'score_top1': .20, 'score_top3': .40, 'wdl': .65, 'actual_ou': .70}
PROTOCOLS = {
    ORIGINAL_SCHEMA: ORIGINAL_TARGETS,
    'WCPP-KPI-REGISTRATION-2': {**ORIGINAL_TARGETS, 'wdl': .60},
}
CURRENT_SCHEMA = ORIGINAL_SCHEMA
TARGETS = PROTOCOLS[CURRENT_SCHEMA]
