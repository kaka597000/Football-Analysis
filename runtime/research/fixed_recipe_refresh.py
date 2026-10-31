"""Inactive, append-only data refresh; never selects recipes, weights or alpha.

Historical refits/replays are development diagnostics, not historical-asof
forecasts or prospective Primary evidence. Production source files stay intact.
"""
import argparse
import copy
import hashlib
import gzip
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'v5'))
sys.path.insert(0, str(ROOT / 'v6'))
import weighted_model as weighted
import ml_model as ml
legacy = weighted.legacy
COHORTS = ('ita.1', 'swe.1')
PROTOCOL = 'WCPP-DATA-REFRESH-FIXED-RECIPE-1'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    path = Path(path)
    if path.suffix == '.gz':
        with gzip.open(path, 'rt') as stream: return json.load(stream)
    return json.loads(path.read_text())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def source_url(value, cohort):
    parsed = urlparse(value or '')
    if parsed.scheme != 'https' or parsed.netloc != 'site.api.espn.com':
        raise ValueError('unreviewed source domain')
    prefix = '/apis/site/v2/sports/soccer/' + cohort + '/'
    if parsed.path not in (prefix + 'summary', prefix + 'scoreboard'):
        raise ValueError('source competition/path mismatch')
    if set(parse_qs(parsed.query)) - {'event', 'dates', 'limit'}:
        raise ValueError('unreviewed source query')


def identity(row):
    return (row['competition'], row['cohort'], row['stage'], row['date'],
            row['home'], row['away'], row['home_goals'], row['away_goals'],
            row['score_basis'], row['neutral'])


def event_key(row):
    key = row.get('score_evidence', {}).get('source_key')
    if not isinstance(key, str) or not re.fullmatch(r'espn:\d+', key):
        raise ValueError('explicit ESPN event identity required')
    return key


def verify_raw(row, raw_root):
    """Check new labels/identity/neutral against the retained actual response."""
    evidence = row.get('score_evidence', {})
    raw_path = Path(evidence.get('source_path', ''))
    if not raw_path.is_absolute(): raw_path = Path(raw_root).parent / raw_path
    if not raw_path.is_file() or not raw_path.resolve().is_relative_to(Path(raw_root).resolve()):
        raise ValueError('missing or out-of-scope retained raw response')
    raw = read(raw_path)
    # Some collectors retain a small provenance envelope around the response.
    payload = raw.get('data', raw) if isinstance(raw, dict) else raw
    if not isinstance(payload, dict):
        raise ValueError('raw ESPN object required')
    events = payload.get('events') or [payload.get('header', payload)]
    event_id = event_key(row).split(':')[1]
    events = [e for e in events if str(e.get('id')) == event_id]
    if len(events) != 1:
        raise ValueError('raw event identity not unique')
    event = events[0]
    league = payload.get('header', {}).get('league', {}).get('slug')
    if league and league != row['cohort']: raise ValueError('raw league conflict')
    competitions = event.get('competitions', [])
    if len(competitions) != 1:
        raise ValueError('raw competition identity ambiguous')
    comp = competitions[0]
    state = comp.get('status', event.get('status', {})).get('type', {})
    if state.get('completed') is not True or state.get('name') != 'STATUS_FULL_TIME':
        raise ValueError('raw status not regulation full time')
    if type(comp.get('neutralSite')) is not bool or comp['neutralSite'] != row['neutral']:
        raise ValueError('raw neutral unknown/conflict')
    sides = {c.get('homeAway'): c for c in comp.get('competitors', [])}
    if set(sides) != {'home', 'away'}:
        raise ValueError('raw home/away identity ambiguous')
    for side in ('home', 'away'):
        c = sides[side]
        if row[side] != 'espn:' + str(c.get('team', {}).get('id')):
            raise ValueError('raw team identity conflict')
        periods = c.get('linescores', [])
        if len(periods) != 2 or any(not str(p.get('displayValue', '')).isdigit() for p in periods):
            raise ValueError('two regulation period labels required')
        if sum(int(p['displayValue']) for p in periods) != row[side + '_goals']:
            raise ValueError('regulation-period score conflict')
        score = c.get('score')
        if isinstance(score, dict): score = score.get('value', score.get('displayValue'))
        if str(score) not in (str(row[side + '_goals']), str(float(row[side + '_goals']))):
            raise ValueError('raw score conflict')
    kickoff = legacy.aware(event.get('date') or comp.get('date') or '')
    minimum_finish = kickoff + timedelta(minutes=90)
    if minimum_finish > legacy.aware(row['observed_at']) or any(
            minimum_finish > legacy.aware(row[name]['query_time']) for name in ('score_evidence','neutral_evidence')):
        raise ValueError('FT label/evidence precedes raw kickoff plus 90 minutes')
    raw_date = kickoff.date().isoformat()
    if raw_date != row['date']:
        raise ValueError('raw calendar date conflict')
    actual_hash = sha256(raw_path)
    if evidence.get('response_sha256') != actual_hash or row['neutral_evidence'].get('response_sha256') != actual_hash:
        raise ValueError('raw checksum conflict')
    return {'path': str(raw_path.resolve()), 'sha256': actual_hash, 'event': event_key(row)}


def validate_new(row, cohort, cutoff, raw_root):
    if row.get('cohort') != cohort or row.get('competition') != cohort or row.get('stage') != 'regular':
        raise ValueError('cross-cohort/phase row')
    if row.get('status') != 'FT' or row.get('score_basis') != 'regulation_90':
        raise ValueError('explicit FT regulation_90 required')
    if type(row.get('neutral')) is not bool:
        raise ValueError('neutral unknown')
    if row['neutral']: raise ValueError('baseline neutral unsupported; separate fix required')
    for key in ('home_goals', 'away_goals'):
        if type(row.get(key)) is not int or not 0 <= row[key] <= 30:
            raise ValueError('invalid regulation score')
    for side in ('home', 'away'):
        if not re.fullmatch(r'espn:\d+', row.get(side, '')):
            raise ValueError('explicit team identity required')
    if row['home'] == row['away'] or not isinstance(row.get('match_id'), str) or not row['match_id']:
        raise ValueError('invalid match identity')
    day = date.fromisoformat(row['date'])
    now = datetime.now(timezone.utc)
    observed = legacy.aware(row['observed_at'])
    if observed > cutoff or cutoff > now or day > observed.date() or day > cutoff.date():
        raise ValueError('future row/observation/cutoff')
    source_url(row.get('source_url'), cohort)
    for name in ('score_evidence', 'neutral_evidence'):
        ev = row.get(name, {})
        if not ev.get('query_time') or legacy.aware(ev['query_time']) > observed:
            raise ValueError('evidence time missing/later than observation')
        if ev.get('source_url'): source_url(ev['source_url'], cohort)
    if row['neutral_evidence'].get('neutral_site') != row['neutral']:
        raise ValueError('neutral evidence conflict')
    for key in ('first_known_at', 'kickoff_at'):
        if row.get(key) and legacy.aware(row[key]) > observed:
            raise ValueError('future ' + key)
    for name in ('xg_evidence', 'process_evidence'):
        if row.get(name):
            raise ValueError('new process/xG needs separate review; preserve missing values')
    if any(k in row for k in ('home_xg', 'away_xg', 'process')):
        raise ValueError('new process/xG values not authorized in result-only refresh')
    return verify_raw(row, raw_root)


def merge_history(old, additions, cohort, cutoff, raw_root):
    # The old dictionaries (including real process/xG evidence) remain exact.
    old = legacy.history(copy.deepcopy(old))
    if any(r['cohort'] != cohort for r in old): raise ValueError('old cohort mismatch')
    merged = list(old)
    ids, events, fixtures = {}, {}, {}
    def register(row, permit_identical):
        keys = (row['match_id'], event_key(row), (row['date'], row['home'], row['away']))
        collisions = [mapping[key] for mapping, key in zip((ids, events, fixtures), keys) if key in mapping]
        if collisions:
            if any(identity(r) != identity(row) for r in collisions):
                raise ValueError('duplicate identity/score conflict')
            if not permit_identical: raise ValueError('duplicate new row')
            return False
        for mapping, key in zip((ids, events, fixtures), keys): mapping[key] = row
        return True
    for row in old: register(row, False)
    raws = []
    for row in additions:
        raws.append(validate_new(row, cohort, cutoff, raw_root))
        if register(row, True): merged.append(copy.deepcopy(row))
    merged = legacy.history(merged)
    if len(merged) == len(old): raise ValueError('refresh contains no new rows')
    for original in old:
        if ids[original['match_id']] != original: raise ValueError('old feature mutation')
    return merged, raws


def coverage_check(coverage, cohort, start, end, additions):
    """A complete claim needs dates AND an event-id inventory; missing proof blocks."""
    scope = coverage.get('cohorts', {}).get(cohort, {})
    days = {(start + timedelta(days=i)).isoformat() for i in range((end-start).days + 1)}
    expected = set(scope.get('expected_finished_event_ids', []))
    actual = {event_key(r) for r in additions}
    reasons = []
    if set(scope.get('checked_dates', [])) != days: reasons.append('date inventory incomplete/unverified')
    if not scope.get('inventory_verified'): reasons.append('finished-event inventory not independently verified')
    if expected != actual: reasons.append('finished-event inventory differs from accepted rows')
    if scope.get('missing') or scope.get('unresolved'): reasons.append('missing/unresolved source rows')
    return {'status': 'COMPLETE_RESEARCH_ONLY' if not reasons else 'PARTIAL_BLOCKED',
            'reasons': reasons, 'start': start.isoformat(), 'end': end.isoformat(),
            'expected_finished': len(expected), 'accepted': len(actual)}


def current_refit(original, merged, built, report_hash):
    if original['config'] != weighted.CONFIG or original['code_hash'] != weighted.code_hash():
        raise ValueError('current code/config mismatch')
    anchor = original['anchor']
    if anchor['selected_recipe'] != 'baseline' or anchor['execution_recipe'] != 'baseline' or anchor['form'] is not None:
        raise ValueError('refresh supports original baseline recipe only')
    if anchor['config'] != legacy.CONFIG or anchor['code_hash'] != legacy.code_hash():
        raise ValueError('anchor code/config mismatch')
    candidate = copy.deepcopy(original)
    updates = {'history': merged, 'core': legacy.fit_core(merged), 'built_at': built,
               'observed_at': max((r['observed_at'] for r in merged), key=legacy.aware), 'train_end': merged[-1]['date']}
    candidate.update(copy.deepcopy(updates))
    candidate['anchor'].update(copy.deepcopy(updates))
    candidate['report_hash'] = report_hash
    candidate.update(formal_validation=False, formal_kpi_eligible=False)
    return candidate


def ml_refit(original, merged, built, report_hash):
    if original['config'] != ml.CONFIG or original['code_hash'] != ml.code_hash() or original['feature_names'] != ml.FEATURES:
        raise ValueError('ML code/config/features mismatch')
    items, folds = ml.rolling_items(merged, merged)
    if any(f['fit_end'] >= f['first_target'] for f in folds):
        raise ValueError('rolling training leakage')
    booster = ml.fit_booster(items)
    candidate = copy.deepcopy(original)
    candidate.update(history=copy.deepcopy(merged), core=legacy.fit_core(merged),
                     booster_model=booster.model_to_string(), built_at=built,
                     observed_at=max((r['observed_at'] for r in merged), key=legacy.aware), train_end=merged[-1]['date'],
                     report_hash=report_hash, formal_validation=False, formal_kpi_eligible=False,
                     feature_importance_gain=dict(zip(ml.FEATURES, booster.feature_importance('gain').tolist())),
                     feature_xg_match_count=sum(bool(r.get('xg_evidence')) for r in merged),
                     feature_process_match_count=sum(bool(r.get('process_evidence')) for r in merged))
    # Alpha is copied, never selected; no validation/test score is inspected.
    trained_ids = {it['row']['match_id'] for it in items}
    excluded = [r['match_id'] for r in merged if r['match_id'] not in trained_ids]
    return candidate, {'folds': folds, 'training_matches': len(items), 'alpha': candidate['alpha'],
                       'training_target_id_digest': legacy.digest(sorted(trained_ids)),
                       'excluded_training_targets': excluded,
                       'exclusion_reason': 'monthly strictly-prior history below fixed warmup_matches'}


def offline_replay(current, test, fixture):
    """Load real serialized artifacts; bypass no forecast guard, use distribution API.

    This is explicitly a postmatch numerical replay, never a prematch freeze.
    """
    import numpy as np
    if fixture.get('neutral') is not False: raise ValueError('baseline neutral unsupported/unknown')
    if current['code_hash'] != weighted.code_hash() or current['config'] != weighted.CONFIG:
        raise ValueError('current load mismatch')
    if test['code_hash'] != ml.code_hash() or test['config'] != ml.CONFIG:
        raise ValueError('ML load mismatch')
    if current['history'] != current['anchor']['history'] or current['core'] != current['anchor']['core']:
        raise ValueError('current anchor load mismatch')
    base, _ = legacy.distribution(current['anchor']['core'], current['anchor']['form'],
                                  current['anchor']['history'], fixture, current['anchor']['execution_recipe'])
    rates = legacy.rates(test['core']['long'], fixture)[:2]
    features = ml.feature_matrix(test['history'], fixture, rates)
    booster = ml.lgb.Booster(model_str=test['booster_model'])
    mm, _ = ml.ml_matrix(booster, features, rates, test['core']['long']['nuisance']['dixon_coles'])
    dc, _ = legacy.distribution(test['core'], None, test['history'], fixture, 'dc365')
    mm = (1-test['alpha'])*dc + test['alpha']*mm
    def summarize(matrix):
        return {'matrix_hash': legacy.digest(matrix.tolist()),
                'primary': '-'.join(map(str, np.unravel_index(np.argmax(matrix), matrix.shape))),
                'means': [float(np.arange(51) @ matrix.sum(axis=i)) for i in (1, 0)],
                'sum': float(matrix.sum())}
    return {'mode': 'POSTMATCH_OFFLINE_NUMERICAL_REPLAY_NOT_ASOF_NOT_FROZEN',
            'fixture': {k: fixture[k] for k in ('match_id', 'date', 'home', 'away', 'neutral')},
            'current': summarize(base), 'test': summarize(mm)}


def run(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    registry = read(args.registry)
    tracked = [Path(args.registry)]
    originals = {}
    for co in COHORTS:
        route = registry['models'][co]
        for key, checksum in (('artifact_path', 'artifact_sha256'), ('test_artifact_path', 'test_artifact_sha256')):
            if sha256(route[key]) != route[checksum]: raise ValueError('registered original hash mismatch')
        originals[co] = {name: read(route[key]) for name, key in
                         [('current', 'artifact_path'), ('test', 'test_artifact_path')]}
        tracked.extend(Path(route[k]) for k in ('artifact_path', 'test_artifact_path'))
    # Record protocol BEFORE seeing incoming labels or fitting anything.
    hashes = {str(p): sha256(p) for p in tracked}
    protocol = {'protocol': PROTOCOL, 'status': 'LOCKED_INACTIVE_RESEARCH', 'cohorts': list(COHORTS),
                'cutoff_at': args.cutoff, 'source_start': '2026-09-21', 'source_end': args.end,
                'original_sha256': hashes, 'algorithm_hashes': {'weighted': weighted.code_hash(), 'main': legacy.code_hash(), 'ml': ml.code_hash()},
                'rules': ['fixed recipes/config/features/weights/alpha; data-only refit',
                          'no historic-asof or prospective gain claim; first_known_at missing',
                          'no activity registry change; no automatic promotion',
                          'Primary Top1 future paired first-prematch freeze is highest KPI'],
                'execution_code_sha256': sha256(__file__)}
    write_new(out/'protocol.json', protocol)
    additions = read(args.rows)
    if isinstance(additions, dict): additions = additions['rows']
    coverage = read(args.coverage)
    cutoff = legacy.aware(args.cutoff)
    if any(r.get('cohort') not in COHORTS for r in additions): raise ValueError('cross-cohort input')
    audit = {'accepted': [], 'rejected': [], 'duplicates': [], 'conflicting': []}
    approved = []
    for row in additions:
        try:
            validate_new(row, row['cohort'], cutoff, args.raw_root)
            approved.append(row)
            audit['accepted'].append({'match_id': row['match_id'], 'event': event_key(row)})
        except (ValueError, KeyError, TypeError) as exc:
            audit['rejected'].append({'match_id': row.get('match_id'), 'reason': str(exc)})
    additions = approved
    # Inventory duplicates/conflicts across IDs, source event IDs, and fixture identity.
    unique = []
    seen_ids, seen_events, seen_fixtures = {}, {}, {}
    for row in additions:
        keys = (row['match_id'], event_key(row), (row['cohort'],row['date'],row['home'],row['away']))
        collisions = [m[k] for m,k in zip((seen_ids,seen_events,seen_fixtures), keys) if k in m]
        if collisions:
            if any(identity(r) != identity(row) for r in collisions):
                audit['conflicting'].append({'match_id':row['match_id'],'reason':'identity/score conflict'})
            else:
                audit['duplicates'].append({'match_id':row['match_id'],'reason':'identical event deduplicated'})
            continue
        for mapping,key in zip((seen_ids,seen_events,seen_fixtures),keys): mapping[key]=row
        unique.append(row)
    additions = unique
    write_new(out/'row-audit.json', audit)
    if audit['conflicting']: raise ValueError('conflicting input rows; stopped before fitting')
    manifest = {'protocol': PROTOCOL, 'active': False, 'formal_promotion': False,
                'historical_asof_eligible': False, 'formal_kpi_eligible': False, 'cohorts': {},
                'inputs': {str(p): sha256(p) for p in (args.rows, args.coverage)},
                'rejected_count':len(audit['rejected']), 'duplicate_count':len(audit['duplicates'])}
    for co in COHORTS:
        rows = [r for r in additions if r['cohort'] == co]
        current_old, ml_old = originals[co]['current'], originals[co]['test']
        current_rows, raws = merge_history(current_old['history'], rows, co, cutoff, args.raw_root)
        ml_rows, _ = merge_history(ml_old['history'], rows, co, cutoff, args.raw_root)
        if any(not '2026-09-21' <= r['date'] <= args.end for r in rows): raise ValueError('source date outside locked interval')
        coverage_result = coverage_check(coverage, co, date(2026, 9, 21), date.fromisoformat(args.end), rows)
        report = {'cohort': co, 'coverage': coverage_result, 'old_current_rows': len(current_old['history']),
                  'new_current_rows': len(current_rows), 'old_ml_rows': len(ml_old['history']),
                  'new_ml_rows': len(ml_rows), 'new_xg_added': False, 'raw': raws,
                  'historical_asof_eligible': False, 'formal_validation': False,
                  'acceptance_carried_from_old': True, 'acceptance_recomputed': False}
        built = datetime.now(timezone.utc).isoformat()
        current = current_refit(current_old, current_rows, built, legacy.digest(report))
        test, training = ml_refit(ml_old, ml_rows, built, legacy.digest(report))
        report['ml_refit'] = training
        final_report_hash = legacy.digest(report)
        current['report_hash'] = test['report_hash'] = final_report_hash
        for artifact in (current, test):
            artifact['acceptance_scope'] = 'ORIGINAL_ARTIFACT_ONLY_NOT_VALIDATION_OF_REFRESHED_MODEL'
            artifact['refresh_validation'] = {'status':'NOT_PROSPECTIVELY_VALIDATED',
                                                'historical_asof_eligible':False,
                                                'coverage_status':coverage_result['status'],
                                                'historical_forward_diagnostic':'NOT_RUN',
                                                'metrics_recomputed':False}
        for artifact in (current, test):
            artifact['refresh_experiment'] = {'protocol': PROTOCOL, 'candidate_id': out.name + '/' + co,
                                              'active':False, 'coverage_status':coverage_result['status'],
                                              'historical_asof_eligible':False, 'acceptance_metrics':'CARRIED_FROM_ORIGINAL_NOT_REFRESH_VALIDATION'}
        write_new(out/co/'current-artifact.json', current)
        write_new(out/co/'test-artifact.json', test)
        write_new(out/co/'report.json', report)
        # Reload the exact written models and a real newly finished fixture.
        fixture = sorted(rows, key=lambda r: (r['date'], r['match_id']))[0]
        old_replay = offline_replay(current_old, ml_old, fixture)
        new_replay = offline_replay(read(out/co/'current-artifact.json'), read(out/co/'test-artifact.json'), fixture)
        write_new(out/co/'offline-replay.json', {'old': old_replay, 'refreshed': new_replay})
        manifest['cohorts'][co] = {'status': coverage_result['status'], 'ml_status': 'REFITTED_FIXED_ALPHA',
                                   'alpha': test['alpha'], 'current_recipe': current['anchor']['execution_recipe'],
                                   'artifact_sha256': {name: sha256(out/co/name) for name in ('current-artifact.json', 'test-artifact.json')},
                                   'train_end': current['train_end'], 'rows_added': len(current_rows)-len(current_old['history'])}
    manifest['original_hashes_unchanged'] = all(sha256(p) == h for p, h in hashes.items())
    if not manifest['original_hashes_unchanged']: raise ValueError('active inputs changed during execution')
    write_new(out/'candidate-manifest.json', manifest)
    return manifest


def predict_current(candidate, fixture):
    if fixture.get('neutral') is not False: raise ValueError('baseline neutral unsupported/unknown')
    return legacy.predict(candidate['anchor'], fixture)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', default='/workspace/WCPP/runtime/active-model.json')
    for key in ('rows', 'coverage', 'raw-root', 'cutoff', 'end', 'out'):
        parser.add_argument('--' + key, required=True)
    print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
