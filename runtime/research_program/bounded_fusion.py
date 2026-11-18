"""Select one existing residual mixture per league, then gate it once.

All supplied seasons are previously inspected development data. This command
never trains component models, issues forecasts, or changes activation.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

import numpy as np
from . import score_mixture_shadow as selection
from .sequence_history import prepare_rows
from .sequence_models import apply
from .three_arm_model import score_picks
from .three_arm_review import _outcome

BOUNDARY = {'weight_selection_end_exclusive': '2026-01-01', 'gate_end_exclusive': '2026-04-01'}


def metrics(rows):
    if not rows:
        raise ValueError('nonempty matched development partition required')
    return {'n': len(rows), 'statistical_top1_hits': sum(r['score_top1'] for r in rows),
            'wdl_hits': sum(r['wdl'] for r in rows),
            'score_log_loss': float(np.mean([r['score_log_loss'] for r in rows])),
            'wdl_brier': float(np.mean([r['wdl_brier'] for r in rows])),
            'coverage': 1.0}


def choose(partitions):
    """Diagnostic labels cannot select or rescue a recipe after its gate fails."""
    weight, gate, diagnostic = partitions
    selected = selection.select(weight)
    retained = selection.gate(selected, gate[selected], gate[selection.BASELINE])
    return {'selected': selected, 'retained': retained,
            'decision': 'RETAIN_BASELINE_CLOSE_SEARCH' if retained == selection.BASELINE else 'ONE_INACTIVE_RECIPE_FOR_FUTURE_REVIEW',
            'selection_metrics': weight[selected], 'gate_selected': gate[selected],
            'gate_baseline': gate[selection.BASELINE],
            'diagnostic_retained': diagnostic[retained], 'diagnostic_baseline': diagnostic[selection.BASELINE]}


def study(rows, artifact):
    if not rows or any(r.get('historical_proxy') is not True for r in rows):
        raise ValueError('this study accepts only explicitly labelled historical proxy development rows')
    if any(r['game_date'] <= artifact['cutoff_at'][:10] for r in rows):
        raise ValueError('component training must precede every selection row')
    if len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate match')
    groups = defaultdict(list)
    for r in rows:
        groups[r['cohort']].append(dict(r, date=r['game_date'], competition=r['cohort'], stage='regular'))
    if not set(groups) <= {'eng.1', 'esp.1', 'fra.1', 'ger.1', 'ita.1'}:
        raise ValueError('registered development scope is the five domestic regular leagues')
    report, ledger = {}, []
    for cohort, games in sorted(groups.items()):
        partitions = selection.split(games, BOUNDARY)
        summaries = []
        for phase, part in zip(('weight_selection', 'gate', 'diagnostic'), partitions):
            outcomes = defaultdict(list)
            for row in part:
                matrices = apply(artifact, row['baseline_matrix'], row['sequence'])
                for alpha in selection.ALPHAS:
                    key = selection.BASELINE if alpha == 0 else ('lightgbm', alpha)
                    matrix = (1. - alpha) * matrices['baseline'] + alpha * matrices['lightgbm']
                    picks = score_picks(matrix, line=row['market']['total']['line'])
                    outcome = _outcome(dict(picks, matrix=matrix), {'home': row['home_goals'], 'away': row['away_goals']})
                    outcomes[key].append(outcome)
                    ledger.append({'match_id': row['match_id'], 'cohort': cohort, 'stage': 'regular',
                                   'date': row['date'], 'partition': phase, 'alpha': alpha,
                                   'primary': picks['primary'], 'outcome': outcome})
            summaries.append({k: metrics(v) for k, v in outcomes.items()})
        decision = choose(summaries)
        # Every fixed-grid row stays inspectable; no runner-up is selected on gate/diagnostic data.
        decision['grid'] = [{ 'recipe': key, 'metrics': [s[key] for s in summaries]} for key in summaries[0]]
        final, base = decision['diagnostic_retained'], decision['diagnostic_baseline']
        decision['round_status'] = ('CLOSED_DEVELOPMENT_NO_ADVANTAGE'
            if final['statistical_top1_hits'] <= base['statistical_top1_hits'] or not selection.noninferior(final, base)
            else 'RESEARCH_ONLY_REQUIRES_NEW_UNSEEN_PROTOCOL')
        decision['baseline'] = 'same-row fixed market projection; internal selector key goals_only is an adapter alias'
        report[cohort] = decision
    return {'schema': 'WCPP-BOUNDED-FUSION-DEVELOPMENT-1', 'evidence_kind': 'REUSED_HISTORY_DEVELOPMENT_ONLY',
            'scope': 'league-specific regular phase; no cross-league weight sharing', 'boundaries': BOUNDARY,
            'component_training_cutoff': artifact['cutoff_at'], 'historical_quote_clock': 'DATE_ONLY_PROXY_NOT_ASOF',
            'primary_metric': 'statistical_top1_not_comprehensive_frozen_primary', 'cohorts': report,
            'coverage_denominator': '1737 previously prepared eligible study rows; not every calendar fixture',
            'new_forecast_arms': 0, 'automatic_promotion': False}, ledger


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    folder = args.data / 'workspace-updates/20261118-sequence/history'
    inputs = folder / 'samples-2526.jsonl.gz'
    artifact_path = folder / 'season-training-artifact.json'
    result, rows = study(prepare_rows(inputs), json.loads(artifact_path.read_text()))
    result['source_sha256'] = {str(p.relative_to(args.data)) if p.is_relative_to(args.data) else p.name:
                               hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (inputs, artifact_path, Path(__file__))}
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    import gzip
    raw = ''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows).encode()
    (args.out / 'rows.jsonl.gz').write_bytes(gzip.compress(raw, mtime=0))
    print(json.dumps({k: {'selected': v['selected'], 'retained': v['retained'], 'decision': v['decision']}
                      for k, v in result['cohorts'].items()}))
