"""Read-only audit of immutable xG comparison outputs; no reranking or clipping."""
from collections import Counter
from datetime import date
from hashlib import sha256
import argparse
import json
import math
from pathlib import Path

from .xg_comparison import VARIANTS, aware, known_by


def jsonl(path):
    with Path(path).open() as stream:
        for line in stream:
            yield json.loads(line)


def digest(path):
    result = sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def audit_output(folder):
    root = Path(folder)
    manifest = json.loads((root / 'manifest.json').read_text())
    for name, expected in manifest.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or digest(path) != expected:
            raise ValueError('artifact hash mismatch: ' + name)
    protocol = json.loads((root / 'protocol.json').read_text())
    mode = protocol['mode']
    errors = Counter()
    feature_index = {}
    missing = Counter()
    residual_nested_count = 0

    def check_event(event, target, cutoff):
        if event['date'] >= target['date'] or event['match_id'] == target['match_id']:
            errors['nonprior_dependency'] += 1
        if not known_by(event, cutoff, mode):
            errors['dependency_unavailable_at_cutoff'] += 1
        if event.get('provider') and event['provider'] != 'understat':
            errors['mixed_xg_provider'] += 1

    for r in jsonl(root / 'features.jsonl'):
        if r['match_id'] in feature_index:
            errors['duplicate_feature_id'] += 1
        cutoff = aware(r['evaluation_cutoff'])
        feature_index[r['match_id']] = {k: r[k] for k in ('date', 'evaluation_cutoff', 'label_first_known_at', 'kickoff_at')}
        if set(r['features']) != set(VARIANTS):
            errors['missing_feature_arm'] += 1
        for side in ('home', 'away'):
            for kind in ('goals', 'xg'):
                for event in r['dependencies'][side][kind]:
                    check_event(event, r, cutoff)
            for event in r['fresh_dependencies'][side]:
                age = (date.fromisoformat(r['date']) - date.fromisoformat(event['date'])).days
                if not 0 < age <= protocol['max_xg_age_days']:
                    errors['stale_fresh_dependency'] += 1
            if not r['fresh_dependencies'][side]:
                missing['fresh_team_rows'] += 1
            if not r['residual_dependencies'][side]:
                missing['residual_team_rows'] += 1
            for event in r['residual_dependencies'][side]:
                check_event(event, r, cutoff)
                historical_cutoff = aware(event['opponent_baseline_cutoff'])
                for dependency in event['opponent_dependencies']:
                    check_event(dependency, event, historical_cutoff)
                    residual_nested_count += 1
        for x in r['features'].values():
            if any(v is not None and not math.isfinite(v) for row in x for v in row):
                errors['nonfinite_feature'] += 1
    fits = {}
    for fit in jsonl(root / 'fits.jsonl'):
        fits[fit['model_id']] = fit
        boundary = aware(fit['fit_boundary'])
        ids = fit['training_match_ids']
        if len(ids) != len(set(ids)) or sha256('\n'.join(ids).encode()).hexdigest() != fit['training_match_ids_sha256']:
            errors['training_id_identity'] += 1
        if set(fit['models']) != set(VARIANTS):
            errors['missing_fit_arm'] += 1
        for mid in ids:
            r = feature_index[mid]
            if r['date'] >= boundary.date().isoformat() or aware(r['evaluation_cutoff']) >= boundary:
                errors['training_date_cutoff'] += 1
            if not known_by({'first_known_at': r['label_first_known_at'], 'kickoff_at': r['kickoff_at']}, boundary, mode, strict=True):
                errors['training_label_availability'] += 1
    prediction_ids = set()
    extreme = {v: [] for v in VARIANTS}
    maxima = {v: {'home': 0., 'away': 0.} for v in VARIANTS}
    for r in jsonl(root / 'predictions.jsonl'):
        if r['match_id'] in prediction_ids or r['match_id'] not in feature_index:
            errors['prediction_identity'] += 1
        prediction_ids.add(r['match_id'])
        fit = fits[r['model_id']]
        if r['match_id'] in fit['training_match_ids'] or aware(fit['fit_boundary']) > aware(r['evaluation_cutoff']):
            errors['target_in_fit_or_late_fit'] += 1
        if set(r['models']) != set(VARIANTS):
            errors['missing_prediction_arm'] += 1
        for variant, result in r['models'].items():
            if abs(sum(result['wdl_probs']) - 1.) > 1e-8:
                errors['probability_mass'] += 1
            for side in ('home', 'away'):
                lam = result['lambda_' + side]
                if not math.isfinite(lam) or lam <= 0:
                    errors['invalid_lambda'] += 1
                maxima[variant][side] = max(maxima[variant][side], lam)
            # Descriptive only; never clips or changes eligibility and metrics.
            if max(result['lambda_home'], result['lambda_away']) > 10:
                extreme[variant].append({'match_id': r['match_id'], 'date': r['date'],
                                         'model_id': r['model_id'], 'lambda_home': result['lambda_home'],
                                         'lambda_away': result['lambda_away']})
    return {'schema': 'WCPP-XG-COMPARISON-AUDIT-1', 'mode': mode,
            'verified_manifest_files': len(manifest), 'feature_rows': len(feature_index),
            'paired_prediction_rows': len(prediction_ids), 'fits': len(fits),
            'residual_nested_dependencies_checked': residual_nested_count,
            'violations': dict(errors), 'temporal_integrity_passed': not errors,
            'availability_certified_by_this_audit': False,
            'missingness': dict(missing), 'maximum_lambda': maxima,
            'diagnostic_fixture_count_with_lambda_over_10': {v: len(rows) for v, rows in extreme.items()},
            'extreme_lambda_examples': {v: sorted(rows, key=lambda r: max(r['lambda_home'], r['lambda_away']), reverse=True)[:5]
                                        for v, rows in extreme.items()},
            'notice': 'Finite probabilities do not establish a sound model; extreme rates remain in all metrics. No candidate selected.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    result = audit_output(args.run)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    raise SystemExit(bool(result['violations']))
