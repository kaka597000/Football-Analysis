"""Daily availability-gated result-state replay; research only, no active writes.

All fixtures of one UTC day share a state snapshot, excluding that day's results.
The fixed arm keeps the common origin state; the rolling arm absorbs only verified
prior-day results available by the independently specified daily cutoff.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
from automation.trainer import _engine
from optimization.evaluate import paired, score_prediction

VERSION = 'ROLLING-RESULT-STATE-SHADOW-1'
CONFIG = {'half_life': 365, 'ridge': 30}
FIXTURE_KEYS = ('match_id', 'cohort', 'competition', 'season', 'date',
                'home', 'away', 'neutral', 'stage', 'kickoff_at')
IDENTITY_KEYS = ('cohort', 'competition', 'date', 'home', 'away', 'kickoff_at')


def stamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def fixture(row):
    # Target outcomes/process/xG and arbitrary extra fields cannot reach models.
    return {k: row.get(k) for k in FIXTURE_KEYS}


def verified_result(row):
    """Adapter must verify source bytes and identity before marking VERIFIED."""
    try:
        e = row['result_evidence']
        if e.get('status') != 'VERIFIED_RAW_REGULATION_90':
            return False
        if not e.get('sha256') or not e.get('source_url') or row.get('score_basis') != 'regulation_90':
            return False
        if any(type(row.get(k)) is not int or not 0 <= row[k] <= 40
               for k in ('home_goals', 'away_goals')):
            return False
        kickoff, known = stamp(row['kickoff_at']), stamp(row['result_available_at'])
        if kickoff.date().isoformat() != row['date']:
            return False
        if known < stamp(e['captured_at']) or known <= kickoff:
            return False
        return bool(row['home'] and row['away'] and row['home'] != row['away'])
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def state_asof(results, cohort, day, cutoff):
    """Latest verified version known at cutoff, strictly before the match day."""
    selected = {}
    for row in results:
        if row.get('cohort') != cohort or row.get('date', '9999') >= day:
            continue
        if not verified_result(row) or stamp(row['result_available_at']) > cutoff:
            continue
        key = row['match_id']; old = selected.get(key)
        if old is not None:
            if any(old[k] != row[k] for k in IDENTITY_KEYS):
                raise ValueError('conflicting result identity')
            a, b = stamp(old['result_available_at']), stamp(row['result_available_at'])
            if a == b and digest(old) != digest(row):
                raise ValueError('ambiguous simultaneous result versions')
            if b < a:
                continue
        selected[key] = copy.deepcopy(row)
    return sorted(selected.values(), key=lambda r: (r['date'], r['match_id']))


def fixture_reason(row, cutoff):
    try:
        if row['home'] == row['away'] or not row['home'] or not row['away']:
            return 'INVALID_FIXTURE_IDENTITY'
        if type(row['neutral']) not in (bool, type(None)):
            return 'INVALID_VENUE'
        kickoff = stamp(row['kickoff_at'])
        if kickoff.date().isoformat() != row['date'] or cutoff >= kickoff:
            return 'CUTOFF_NOT_BEFORE_KICKOFF'
        e = row['fixture_evidence']
        if e.get('verified') is not True or not e.get('sha256'):
            return 'UNVERIFIED_PREMATCH_FIXTURE'
        if stamp(e['captured_at']) > cutoff:
            return 'FIXTURE_NOT_YET_AVAILABLE'
        if not e.get('source_url'):
            return 'MISSING_FIXTURE_SOURCE'
    except (KeyError, TypeError, ValueError, AttributeError):
        return 'MISSING_PREMATCH_FIXTURE_EVIDENCE'
    return None


def run(fixtures, results, *, origin_at, evaluation_at, day_cutoffs=None,
        config=None, engine=None):
    """Pure replay: no files, active models, original freezes or sources changed.

    Cutoffs must be fixed independently of future captures/results. Both arms
    retain every target, including pending and abstained matches. The existing
    core needs at least 80 verified historical rows per independent cohort.
    """
    origin, evaluation = stamp(origin_at), stamp(evaluation_at)
    if evaluation <= origin:
        raise ValueError('evaluation must follow origin')
    config = dict(CONFIG if config is None else config)
    engine = engine or _engine()
    targets = sorted(copy.deepcopy(fixtures), key=lambda r: (r['date'], r['match_id']))
    ids = [r['match_id'] for r in targets]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate target match_id')
    identity = {r['match_id']: {k: r.get(k) for k in IDENTITY_KEYS} for r in targets}
    for row in results:
        if not verified_result(row):
            continue
        fields = {k: row.get(k) for k in IDENTITY_KEYS}
        if identity.setdefault(row['match_id'], fields) != fields:
            raise ValueError('result/fixture identity mismatch: '+row['match_id'])
    cutoffs = day_cutoffs or {}
    cohorts = sorted({r['cohort'] for r in targets})
    fixed_rows = {c: state_asof(results, c, origin.date().isoformat(), origin) for c in cohorts}
    models = ('FIXED_STATE', 'DAILY_ROLLING')
    predictions = {m: [] for m in models}
    audit, fit_cache = [], {}

    def fit(rows):
        key = digest(rows)
        if key not in fit_cache:
            if len(rows) < 80:
                raise ValueError('INSUFFICIENT_VERIFIED_HISTORY')
            safe = [{**fixture(r), 'home_goals': r['home_goals'], 'away_goals': r['away_goals']} for r in rows]
            fit_cache[key] = engine.fit_core(safe, **config)
        return fit_cache[key]

    for day in sorted({r['date'] for r in targets}):
        cutoff = stamp(cutoffs.get(day, day+'T00:00:00+00:00'))
        if cutoff < origin or cutoff >= evaluation or cutoff.date().isoformat() != day:
            raise ValueError('invalid common daily cutoff')
        block = [r for r in targets if r['date'] == day]
        for cohort in sorted({r['cohort'] for r in block}):
            state = {'FIXED_STATE': fixed_rows[cohort],
                     'DAILY_ROLLING': state_asof(results, cohort, day, cutoff)}
            fitted, failure = {}, None
            for name in models:
                try:
                    fitted[name] = fit(state[name])
                except ValueError as exc:
                    failure = str(exc)
            audit.append({'date': day, 'cohort': cohort, 'cutoff_at': cutoff.isoformat(),
                'state': {m: {'n': len(state[m]), 'sha256': digest(state[m]),
                    'match_ids': [r['match_id'] for r in state[m]],
                    'latest_available_at': max((r['result_available_at'] for r in state[m]), default=None)} for m in models},
                'updates_applied_within_day': 0})
            for target in [r for r in block if r['cohort'] == cohort]:
                reason = fixture_reason(target, cutoff) or failure
                safe = fixture(target)
                for name in models:
                    pred = {'match_id': safe['match_id'], 'cohort': cohort, 'date': day,
                        'model': name, 'cutoff_at': cutoff.isoformat(),
                        'status': 'ABSTAIN' if reason else 'PREDICTED', 'reason': reason,
                        'formal_kpi_eligible': False, 'fixture_hash': digest(safe),
                        'state_hash': digest(state[name]), 'state_n': len(state[name])}
                    if not reason:
                        rates = engine.rates(fitted[name], [safe])[0]
                        m = engine.matrix(rates)
                        primary = list(map(int, np.unravel_index(np.argmax(m), m.shape)))
                        pred.update(matrix=m.tolist(), primary=primary, rates=rates.tolist(),
                                    primary_probability=float(m[tuple(primary)]))
                    predictions[name].append(pred)
    # Settlement is separate and starts only after all predictions have finished.
    score_labels = {r['match_id']: r for c in cohorts
                    for r in state_asof(results, c, '9999-12-31', evaluation)}
    scores = {m: [score_prediction(p, score_labels.get(p['match_id'])) for p in predictions[m]] for m in models}
    return {'version': VERSION, 'mode': 'CAPTURE_AVAILABILITY_GATED_RETROSPECTIVE_SHADOW',
        'config': config, 'origin_at': origin.isoformat(), 'evaluation_at': evaluation.isoformat(),
        'day_timezone': 'UTC', 'predictions': predictions, 'scores': scores, 'daily_state_audit': audit,
        'comparison': paired(scores['FIXED_STATE'], scores['DAILY_ROLLING']),
        'target_ids': ids, 'target_ids_sha256': digest(ids), 'formal_validation': False,
        'active_changed': False, 'automatic_promotion': False,
        'limitations': ['Retrospective algorithm replay, not predictions issued before kickoff.',
            'Actual captures prove availability at capture only, not historical first-known.',
            'This fixed M2 core is not the active current/test model.',
            'No xG, process, lineup, market or human-selection adjustments.',
            'All actual-market metrics unavailable unless separately provided.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True); p.add_argument('--out', required=True)
    a = p.parse_args(); answer = run(**json.loads(Path(a.input).read_text()))
    with Path(a.out).open('x') as h:
        json.dump(answer, h, indent=2, allow_nan=False)
    print(json.dumps({'N_scope': len(answer['target_ids']),
        'N_common_scored': answer['comparison']['N_common_scored'],
        'net_top1_hits': answer['comparison']['net_top1_hits'], 'active_changed': False}))


if __name__ == '__main__':
    main()
