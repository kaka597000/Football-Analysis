"""Fit one league-specific total-goal correction on earlier first issues, then grade the locked panel."""
import argparse
from collections import Counter, defaultdict
from datetime import timedelta, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

import numpy as np
from scipy.optimize import brentq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from research.analysis_freeze import aware, digest, verify
from research_program.replay import _finished_espn_label
from research_program.score_coverage_policy import select_scores
from research_program.three_arm_model import _matrix

PROTOCOL = ROOT / 'docs/research/frozen-current-total-protocol-20261124.json'
METRICS = ('top1', 'top3', 'p_four_plus', 'four_plus_brier', 'score_log_loss')


def git_bytes(repo, commit, name):
    return subprocess.check_output(['git', '-C', str(repo), 'show', f'{commit}:{name}'])


def checked(raw, sha):
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError(f'archive evidence differs from {sha}')
    return raw


def member(archive, name):
    return archive.extractfile(name).read()


def original_models(wcpp, inventory, cohorts):
    registry = json.loads(checked((wcpp / 'runtime/active-model.json').read_bytes(),
                                  inventory['WCPP/runtime/active-model.json']))
    models = {}
    for cohort in cohorts:
        ref = registry['models'][cohort]
        relative = Path(ref['artifact_path']).relative_to('/workspace/WCPP')
        raw = checked((wcpp / relative).read_bytes(), inventory['WCPP/' + relative.as_posix()])
        anchor = json.loads(checked(raw, ref['artifact_sha256']))['anchor']
        models[cohort] = {'hash': digest(anchor), 'train_end': anchor['train_end'], 'version': anchor['version']}
    return models


def first_current(record, mid, sha, protocol, models):
    verify(record)
    fixture = record['fixture']
    prediction = record['payload'][protocol['role']]
    cutoff = aware(fixture['cutoff_at'])
    model = models[fixture['cohort']]
    if (record['record_sha256'] != sha or record['match_id'] != mid or fixture['match_id'] != mid
            or record['revision'] != 1 or record['stage'] != 'prematch' or record['track'] != 'current-test-pair'
            or record['evaluation_slot'] != 'FIRST_PREMATCH' or fixture['stage'] != protocol['stage']
            or prediction['version'] != protocol['baseline_version'] or prediction['input'] != fixture
            or digest(prediction['matrix']) != prediction['matrix_hash']
            or prediction['model_hash'] != model['hash'] or model['version'] != prediction['version']
            or prediction.get('training_data_cutoff', model['train_end']) != model['train_end']
            or model['train_end'] >= cutoff.date().isoformat()
            or max(aware(prediction['model_built_at']), aware(prediction['model_observed_at'])) > cutoff
            or not cutoff <= aware(prediction['generated_at']) <= aware(record['recorded_at']) < aware(fixture['kickoff_at'])):
        raise ValueError(f'first Current identity, matrix or time conflict: {mid}')
    return fixture, _matrix(prediction['matrix'])


def finished(fixture, score, url, path):
    label = dict(fixture, home_goals=score[0], away_goals=score[1])
    _finished_espn_label(label, {'url': url}, path)


def training_rows(repo, wcpp, manifest_file, protocol):
    settings = protocol['training']
    inventory = json.loads(checked(manifest_file.read_bytes(), settings['files_manifest_sha256']))
    original = {entry['path']: entry['sha256'] for entry in inventory if entry['path'].startswith('WCPP/')}
    available_bytes = set(original.values())
    models = original_models(wcpp, original, protocol['cohorts'])
    archive = git_bytes(repo, settings['inventory_commit'], settings['inventory_archive'])
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
        catalog = json.loads(member(bundle, settings['member']))
    groups, evidence, missing = defaultdict(list), [], {}
    roster = set()
    for item in catalog:
        cohort, mid = item['cohort'], item['match_id']
        if cohort not in protocol['cohorts']:
            continue
        relative = Path(item['path']).relative_to('/workspace/WCPP')
        pair_sha = original.get('WCPP/' + relative.as_posix())
        if pair_sha is None:
            # Only first issues that already existed at the immutable fitting snapshot can enter it.
            continue
        record = json.loads(checked((wcpp / relative).read_bytes(), pair_sha))
        fixture = record['fixture']
        if aware(fixture['kickoff_at']) >= aware(settings['kickoff_before']):
            continue
        if mid in roster:
            raise ValueError(f'duplicate training fixture {mid}')
        roster.add(mid)
        if fixture.get('stage') != protocol['stage']:
            missing[mid] = 'regular_stage_unconfirmed'
            continue
        if item['actual']['status'] != 'LOCALLY_REVERIFIED_FT':
            missing[mid] = 'no_verified_result_in_inventory'
            continue
        sources = [r for r in item['actual']['evidence'] if r['sha256'] in available_bytes]
        if not sources:
            missing[mid] = 'result_bytes_absent_from_early_snapshot'
            continue
        fixture, matrix = first_current(record, mid, item['record_sha256'], protocol, models)
        if fixture['cohort'] != cohort:
            raise ValueError(f'training cohort conflict: {mid}')
        score = item['actual']['score']
        valid = []
        for ref in sources:
            path = wcpp / Path(ref['path']).relative_to('/workspace/WCPP')
            checked(path.read_bytes(), ref['sha256'])
            try:
                finished(fixture, score, ref['source_url'], path)
            except ValueError:
                continue
            valid.append(ref['sha256'])
        if not valid:
            missing[mid] = 'no_matching_two_half_espn_result'
            continue
        groups[cohort].append((matrix, sum(score)))
        evidence.append(dict(match_id=mid, cohort=cohort, freeze_sha256=record['record_sha256'],
                             model_sha256=record['payload'][protocol['role']]['model_hash'], result_sha256=valid))
    return groups, {'roster': len(roster), 'used': evidence, 'missing': missing}, models


def adjust(matrix, beta):
    if beta == 0:
        return matrix
    goals = np.indices(matrix.shape).sum(axis=0)
    adjusted = matrix * np.exp(beta * goals)
    return adjusted / adjusted.sum()


def fit(rows, ridge, bound):
    if not rows:
        return 0.
    totals = []
    for matrix, actual in rows:
        g = np.indices(matrix.shape).sum(axis=0).ravel()
        p = np.bincount(g, weights=matrix.ravel())
        totals.append((np.arange(len(p)), p, actual))

    def gradient(beta):
        derivative = ridge * beta
        for support, mass, actual in totals:
            weighted = mass * np.exp(beta * support)
            derivative += weighted @ support / weighted.sum() - actual
        return derivative

    if gradient(-bound) >= 0:
        return -bound
    if gradient(bound) <= 0:
        return bound
    return float(brentq(gradient, -bound, bound))


def picks(matrix):
    return select_scores([matrix], policy='probability')['reference_scores']


def grade(matrix, scores, actual):
    score = f'{actual[0]}-{actual[1]}'
    total = np.indices(matrix.shape).sum(axis=0)
    tail = float(matrix[total >= 4].sum())
    return dict(scores=scores, top1=int(scores[0] == score), top3=int(score in scores),
                p_four_plus=tail, four_plus_brier=(tail - int(sum(actual) >= 4))**2,
                score_log_loss=-float(np.log(matrix[tuple(actual)])))


def evaluate(repo, protocol, coefficients, train_ids, models):
    spec = protocol['evaluation']
    raw = checked(git_bytes(repo, spec['commit'], spec['archive']), spec['archive_sha256'])
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
        rows = [json.loads(line) for line in gzip.decompress(member(archive, spec['rows'])).splitlines()]
        sources = {s['match_id']: s for s in json.loads(member(archive, spec['source_index']))['rows']}
        brazil = member(archive, 'retained/brazil_retained_inputs.tar.gz')
    with tarfile.open(fileobj=io.BytesIO(brazil), mode='r:gz') as archive:
        nested = {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}

    def read(ref):
        if ref.startswith('brazil_retained_inputs.tar.gz!'):
            return nested[ref.split('!', 1)[1]]
        return git_bytes(repo, spec['commit'], ref)

    output, missing, roster = [], {}, set()
    duplicates = [r['match_id'] for r in rows]
    if len(set(duplicates)) != len(rows) or set(duplicates) != set(sources):
        raise ValueError('evaluation identities conflict with source manifest')
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / 'result.json'
        for row in rows:
            mid, kick = row['match_id'], aware(row['kickoff_at'])
            if not aware(spec['kickoff_from']) <= kick < aware(spec['kickoff_until_exclusive']):
                continue
            if row['cohort'] not in protocol['cohorts']:
                continue
            roster.add(mid)
            source = sources[mid]
            record = json.loads(checked(read(source['freeze_path']), source['freeze_file_sha256']))
            fixture, baseline = first_current(record, mid, source['freeze_record_sha256'], protocol, models)
            current = record['payload'][protocol['role']]
            if (mid in train_ids or fixture['cohort'] != row['cohort'] or aware(fixture['kickoff_at']) != kick
                    or any(fixture[k] != source[k] for k in ('match_id', 'cohort', 'stage', 'home', 'away'))
                    or record['freeze_id'] != source['freeze_id']
                    or current['model_hash'] != source['model_hash']
                    or current['matrix_hash'] != source['matrix_sha256']
                    or digest(row['current_matrix']) != current['matrix_hash']
                    or aware(row['cutoff_at']) != aware(fixture['cutoff_at'])
                    or aware(row['observed_at']) != aware(source['observed_at'])
                    or aware(row['observed_at']) < kick + timedelta(minutes=90)):
                raise ValueError(f'overlap or evaluation identity/time mismatch: {mid}')
            if aware(fixture['cutoff_at']) < aware(protocol['training']['fit_as_of']):
                missing[mid] = 'cutoff_before_fit'
                continue
            changed = adjust(baseline, coefficients[fixture['cohort']])
            b_scores, c_scores = picks(baseline), picks(changed)
            # Only the grader receives scores from after kickoff.
            actual = [row['home_goals'], row['away_goals']]
            path.write_bytes(checked(read(source['raw_result_path']), source['raw_result_sha256']))
            try:
                finished(fixture, actual, source['result_url'], path)
            except ValueError as exc:
                missing[mid] = str(exc)
                continue
            output.append(dict(match_id=mid, cohort=fixture['cohort'], kickoff_at=fixture['kickoff_at'],
                               cutoff_at=fixture['cutoff_at'], date=kick.astimezone(timezone.utc).date().isoformat(),
                               actual=actual, four_plus=int(sum(actual) >= 4),
                               freeze_sha256=record['record_sha256'], matrix_sha256=current['matrix_hash'],
                               model_sha256=current['model_hash'], result_sha256=source['raw_result_sha256'],
                               archived_order_matches=b_scores == [x['score'] for x in current['top3']],
                               baseline=grade(baseline, b_scores, actual), candidate=grade(changed, c_scores, actual)))
    return output, {'retained_panel': len(rows), 'in_window': len(roster), 'evaluated': len(output), 'missing': missing}


def summarize(rows):
    if not rows:
        return {'n': 0}
    result = {'n': len(rows), 'observed_four_plus': sum(r['four_plus'] for r in rows)}
    for arm in ('baseline', 'candidate'):
        result[arm] = {k: (sum(r[arm][k] for r in rows) if k in ('top1', 'top3', 'p_four_plus')
                           else float(np.mean([r[arm][k] for r in rows]))) for k in METRICS}
    for k in ('top1', 'top3'):
        result[k + '_changes'] = {
            'corrected': [r['match_id'] for r in rows if r['candidate'][k] > r['baseline'][k]],
            'spoiled': [r['match_id'] for r in rows if r['candidate'][k] < r['baseline'][k]],
        }
    return result


def date_intervals(rows):
    dates = defaultdict(list)
    for r in rows:
        dates[r['date']].append([r['candidate'][k] - r['baseline'][k] for k in METRICS])
    if not dates:
        return {}
    blocks = list(dates.values())
    totals = np.array([np.sum(b, axis=0) for b in blocks])
    sizes = np.array([len(b) for b in blocks])
    draw = np.random.default_rng(20261124).integers(0, len(blocks), (2000, len(blocks)))
    delta = totals[draw].sum(axis=1) / sizes[draw].sum(axis=1)[:, None]
    return {k: interval.tolist() for k, interval in zip(METRICS, np.quantile(delta, [.025, .975], axis=0).T)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-repo', required=True, type=Path)
    parser.add_argument('--wcpp', required=True, type=Path, help='restored WCPP directory from the immutable snapshot')
    parser.add_argument('--snapshot-files', required=True, type=Path, help='November 2 files.json')
    args = parser.parse_args()
    protocol = json.loads(PROTOCOL.read_bytes())
    if aware(protocol['training']['snapshot_at']) >= aware(protocol['training']['fit_as_of']):
        raise ValueError('training evidence not yet available')
    train, coverage, models = training_rows(args.data_repo, args.wcpp, args.snapshot_files, protocol)
    change = protocol['change']
    coefficients = {c: fit(train[c], change['ridge'], change['max_abs_beta']) for c in protocol['cohorts']}
    rows, held = evaluate(args.data_repo, protocol, coefficients, {r['match_id'] for r in coverage['used']}, models)
    summary = summarize(rows)
    if summary['n']:
        b, c = summary['baseline'], summary['candidate']
        passes = (c['top1'] > b['top1'] and c['top3'] >= b['top3'] and c['score_log_loss'] <= b['score_log_loss']
                  and c['four_plus_brier'] <= b['four_plus_brier'])
    else:
        passes = False
    report = dict(protocol_sha256=hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(), development_only=True,
                  training=coverage, baseline_models=models, coefficients=coefficients, coverage=held, overall=summary,
                  by_cohort={c: summarize([r for r in rows if r['cohort'] == c]) for c in protocol['cohorts']},
                  utc_matchdays=len({r['date'] for r in rows}), paired_date_intervals=date_intervals(rows),
                  decision='ELIGIBLE_FOR_DRAFT_REVIEW_ONLY' if passes else 'RETAIN_BASELINE',
                  prospective_n=0, rows=rows)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
