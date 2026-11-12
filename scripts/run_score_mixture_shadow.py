"""Execute the bounded, inactive score-mixture protocol against retained rows."""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from research_program import score_mixture_shadow as m


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def day_start(value):
    """Source rows explicitly use date-only UTC assumptions, never kickoff claims."""
    return datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=timezone.utc)


def verify_inputs(protocol):
    for item in protocol['inputs'].values():
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('retained input hash mismatch')
    if (protocol['alpha_grid'] != list(m.ALPHAS) or protocol['experts'] != list(m.EXPERTS)
            or protocol['baseline'] != m.BASELINE[0] or protocol['minimum_partition_n'] != 100
            or protocol['guard_tolerance'] != 1e-12):
        raise ValueError('fixed experiment protocol changed')
    rows = [json.loads(line) for line in Path(protocol['inputs']['predictions']['path']).read_text().splitlines()]
    fits = [json.loads(line) for line in Path(protocol['inputs']['fits']['path']).read_text().splitlines()]
    by_model = {r['model_id']: r for r in fits}
    if len(by_model) != len(fits) or len({r['match_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate fixture or fit identity')
    dates = {r['match_id']: r['date'] for r in rows}
    missing_training_dates = set()
    for fit in fits:
        boundary = datetime.fromisoformat(fit['fit_boundary'])
        if boundary.tzinfo is None:
            raise ValueError('fit boundary must carry its timezone')
        if len(fit['training_match_ids']) != fit['training_fixtures']:
            raise ValueError('training population mismatch')
        for match_id in fit['training_match_ids']:
            if match_id in dates and day_start(dates[match_id]) >= boundary:
                raise ValueError('known training label crosses monthly boundary')
            if match_id not in dates:
                missing_training_dates.add(match_id)
    groups = defaultdict(list)
    for row in rows:
        fit = by_model[row['model_id']]
        group = [row['cohort'], row['competition'], row['stage']]
        if (fit['group'] != group or datetime.fromisoformat(fit['fit_boundary']) > day_start(row['date'])
                or row['match_id'] in fit['training_match_ids']):
            raise ValueError('fit identity or temporal boundary mismatch')
        groups['|'.join(group)].append(row)
    if set(groups) != set(protocol['partitions']):
        raise ValueError('cohort population differs from locked protocol')
    for key, records in groups.items():
        identity = [{k: r[k] for k in ('match_id', 'date', 'model_id')}
                    for r in sorted(records, key=lambda r: (r['date'], r['match_id']))]
        if digest(identity) != protocol['partitions'][key]['identity_sha256']:
            raise ValueError('locked cohort identity mismatch')
    return groups, {'fit_count': len(fits), 'known_training_date_violations': 0,
                    'warmup_training_ids_without_dates_in_prediction_file': len(missing_training_dates),
                    'notice': 'Retained upstream monthly fits; this check does not certify historical first-known times.'}


def prediction(row, key):
    expert, alpha = key
    p = m.distribution(row['models']['goals_only'], row['models'][expert], alpha)
    metrics = m.score(p, row['actual_score'])
    if key == m.BASELINE:
        stored = row['models']['goals_only']
        if (p['statistical_top1'] != stored['statistical_top1']
                or any(abs(metrics[k] - stored[k]) > 1e-9 for k in ('score_log_loss', 'wdl_brier', 'wdl_rps'))):
            raise ValueError('reconstructed baseline does not match retained source')
    return p, metrics


def evaluate(rows, key):
    return m.summarize([prediction(row, key)[1] for row in rows])


def run(protocol_path, out):
    protocol_bytes = Path(protocol_path).read_bytes()
    protocol = json.loads(protocol_bytes)
    groups, training_audit = verify_inputs(protocol)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    # The prior protocol binds the grid, identities and date splits. This binds
    # the exact executing code before any metrics or selected weights are read.
    write(out / 'execution-lock.json', {'started_at': datetime.now(timezone.utc).isoformat(),
        'protocol_path': str(protocol_path), 'protocol_sha256': hashlib.sha256(protocol_bytes).hexdigest(),
        'code': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (Path(__file__), Path(m.__file__))}})
    reports, records = {}, []
    keys = [m.BASELINE] + [(expert, alpha) for expert in m.EXPERTS for alpha in m.ALPHAS if alpha]
    for group, rows in sorted(groups.items()):
        boundary = protocol['partitions'][group]
        selection, gate_rows, diagnostic = m.split(rows, boundary)
        counts = [len(selection), len(gate_rows), len(diagnostic)]
        if counts != [boundary[k] for k in ('selection_n', 'gate_n', 'diagnostic_n')]:
            raise ValueError('locked split population differs')
        enough = min(counts) >= protocol['minimum_partition_n']
        # No diagnostic outcome is passed to weight selection or the gate.
        selection_metrics = {key: evaluate(selection, key) for key in keys} if enough else {}
        selected = m.select(selection_metrics) if enough else m.BASELINE
        gate_base, gate_candidate = evaluate(gate_rows, m.BASELINE), evaluate(gate_rows, selected)
        deployed = m.gate(selected, gate_candidate, gate_base) if enough else m.BASELINE
        # Persist selected weights before evaluating this cohort's last segment.
        choice = {'selected_expert': selected[0], 'selected_alpha': selected[1],
                  'gated_expert': deployed[0], 'gated_alpha': deployed[1],
                  'gate_passed': selected == deployed, 'enough_rows': enough,
                  'active_modified': False, 'promotion': False}
        write(out / (group.replace('|', '_') + '-selection-lock.json'), choice)
        cohort_records = []
        for row in diagnostic:
            item = {k: row[k] for k in ('match_id', 'date', 'cohort', 'competition', 'stage', 'model_id', 'actual_score')}
            item.update(predictions={}, metrics={}, weights=choice,
                        partition='REUSED_HISTORY_DIAGNOSTIC', comprehensive_primary=None, actual_line_ou=None)
            for name, key in [('baseline', m.BASELINE), ('pre_gate_candidate', selected), ('gated_candidate', deployed)]:
                p, metrics = prediction(row, key)
                item['predictions'][name], item['metrics'][name] = p, metrics
            cohort_records.append(item)
        records.extend(cohort_records)
        reports[group] = {'weights': choice, 'split': boundary,
            'selection_trials': [{'expert': key[0], 'alpha': key[1], 'metrics': value,
                                  'eligible': m.noninferior(value, selection_metrics[m.BASELINE])}
                                 for key, value in selection_metrics.items()],
            'gate': {'baseline': gate_base, 'candidate': gate_candidate},
            'diagnostic': {name: m.summarize([r['metrics'][name] for r in cohort_records])
                           for name in ('baseline', 'pre_gate_candidate', 'gated_candidate')},
            'paired': {name: m.paired(cohort_records, name) for name in ('pre_gate_candidate', 'gated_candidate')}}
        print(group, json.dumps(choice), flush=True)
    with (out / 'diagnostic-predictions.jsonl').open('x', encoding='utf-8') as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    report = {'schema': 'WCPP-SCORE-MIXTURE-REPORT-1', 'population': sum(len(r) for r in groups.values()),
        'diagnostic_population': len(records), 'diagnostic_predicted': len(records), 'diagnostic_coverage': 1.,
        'cohorts': reports, 'training_audit': training_audit,
        'diagnostic': {name: m.summarize([r['metrics'][name] for r in records])
                      for name in ('baseline', 'pre_gate_candidate', 'gated_candidate')},
        'paired': {name: m.paired(records, name) for name in ('pre_gate_candidate', 'gated_candidate')},
        **{key: protocol[key] for key in ('primary_kpi', 'actual_line_ou', 'historical_asof_certified',
           'new_unseen_holdout', 'prior_history_reused', 'future_effect_verified', 'active_comparator',
           'active_modified', 'promotion', 'interpretation')},
        'weights_use': 'INACTIVE; gated candidates are development diagnostics, not live deployment',
        'scope': 'Five European domestic leagues only; no Nations League, cups or OOD conclusions',
        'outliers_removed': 0, 'post_prediction_rate_clipping': False}
    write(out / 'report.json', report)
    write(out / 'manifest.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted(out.iterdir()) if p.is_file()})
    print(json.dumps({'diagnostic': report['diagnostic'], 'paired': report['paired']}), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    run(args.protocol, args.out)
