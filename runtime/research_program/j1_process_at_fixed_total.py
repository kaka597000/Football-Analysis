"""Isolate paired, pregame J1 process effects inside each original goal total."""

import argparse
from pathlib import Path

import numpy as np

from research.analysis_freeze import DEFAULT_ROOT, aware, digest
from . import goal_state_forward as ledger
from . import j1_fixed_first_backups as r42
from . import j1_process_forward as r28
from .goal_state_study import sha, write_new
from .three_arm_model import score_picks, top_three_scores


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261127-r46-j1-process-at-fixed-total/protocol.json'
CONTROLS = ('common_prior', 'goals_same_total', 'xg_history')


def match_goal_totals(prior, alternative):
    """Preserve joint score ratios at each total and recover the original total PMF."""
    totals = np.indices(prior.shape).sum(axis=0)
    old = np.bincount(totals.ravel(), weights=prior.ravel())
    offered = np.bincount(totals.ravel(), weights=alternative.ravel(), minlength=len(old))
    if np.any((old > 0.) & (offered <= 0.)):
        raise ValueError('a calibrated J1 total lacks prior support')
    factor = np.divide(old, offered, out=np.zeros_like(old), where=offered > 0.)
    changed = alternative * factor[totals]
    error = float(np.max(np.abs(np.bincount(totals.ravel(), weights=changed.ravel()) - old)))
    if error > 2e-14:
        raise ValueError('process replay changed the original goal-total distribution')
    return changed, error


def pregame_row(fixture, pair, form, cutoff, saved):
    native = r28.native_matrices(pair)
    common = np.sqrt(native[0]) * np.sqrt(native[1])
    if common.sum() <= 0.:
        raise ValueError('the two original J1 distributions have no common mass')
    prior = common / common.sum()
    home, away = [form['teams'][fixture[side]] for side in r28.SIDES]
    histories = [r28.matched_observations(home, away, cutoff), r28.matched_observations(away, home, cutoff)]
    adjusted, exposure = r28.process_arms(prior, histories)
    original = saved['payload']
    wdl = score_picks(prior)['wdl']
    reconstructed = dict(common_prior=prior, **adjusted)
    for arm, matrix in reconstructed.items():
        expected = original['forecasts'][arm]
        ordered = top_three_scores(matrix)
        if expected != dict(primary=ordered[0], top3=ordered, wdl=wdl,
                            first_probability=round(float(matrix.max()), 12)):
            raise ValueError('the original R28 score matrix did not reproduce: ' + fixture['match_id'])
    if exposure != original['exposure'] or original['source_pair_sha256'] != pair['record_sha256']:
        raise ValueError('the original R28 match exposure or paired scores differ')
    forecasts = dict(original['forecasts'])
    matrices, errors = {}, {}
    for arm, source in (('xg_same_total', 'xg_history'), ('goals_same_total', 'goals_history')):
        matrix, error = match_goal_totals(prior, adjusted[source])
        ordered = top_three_scores(matrix)
        forecasts[arm] = dict(primary=ordered[0], top3=ordered, wdl=wdl,
                              first_probability=round(float(matrix.max()), 12))
        matrices[arm], errors[arm] = matrix.tolist(), error
    return dict(fixture=fixture, line=original['line'], forecasts=forecasts,
                matrices=dict(common_prior=prior.tolist(), **matrices),
                max_total_projection_error=max(errors.values()), exposure=exposure,
                source_r28_sha256=saved['record_sha256'],
                source_comprehensive_sha256=original['source_comprehensive_sha256'],
                source_pair_sha256=pair['record_sha256'])


def prepare(data, spec):
    registered = data / r28.REGISTRATION
    plan = ledger._read(registered, r28.read(registered / 'plan.json'))
    publication, original = r28.original_batch(data, r28.SECOND_BATCH, 'nov28_29', plan)
    inputs = spec['inputs']
    parent_dir = data / inputs['r28_issued']
    index_path = parent_dir / 'receipts.json'
    if (sha(index_path) != inputs['r28_receipts_sha256'] or sha(r28.PROTOCOL) != inputs['r28_protocol_sha256']
            or plan['freeze_id'] != inputs['original_registration']
            or publication['freeze_id'] != inputs['original_comprehensive_batch'] or len(original) != 10):
        raise ValueError('R46 requires the original immutable R28 and comprehensive approvals')
    parent_spec = r28.read(r28.PROTOCOL)
    if parent_spec['inputs']['form_sha256'] != inputs['form_sha256']:
        raise ValueError('R46 expected the exact earlier process evidence')
    form = r28.verified_form(data, parent_spec)
    cutoff = aware(form['observed_at'])
    index = r28.read(index_path)
    parent = ledger._read(parent_dir, index['registration'])
    earliest = min(aware(row[0]['kickoff_at']) for row in original.values())
    if (parent['freeze_id'] != inputs['r28_registration']
            or set(index['matches']) != set(original)
            or not aware(publication['recorded_at']) <= cutoff <= aware(parent['recorded_at']) < earliest
            or parent['payload']['identity']['process_cutoff_at'] != form['observed_at']):
        raise ValueError('R28 pregame source population, cutoff or registration changed')
    rows, recorded = {}, []
    for mid, (fixture, authored, pair) in original.items():
        saved = ledger._read(parent_dir, index['matches'][mid])
        payload = saved['payload']
        bound = {k: v for k, v in payload.items() if k != 'registration_sha256'}
        if (saved['track'] != parent['payload']['track'] or payload['fixture'] != fixture
                or saved['fixture'] != dict(fixture, cutoff_at=parent['recorded_at'])
                or payload['registration_sha256'] != parent['record_sha256']
                or digest(bound) != parent['payload']['identity']['forecast_sha256'][mid]
                or payload['source_comprehensive_sha256'] != authored['record_sha256']
                or not aware(parent['recorded_at']) <= aware(saved['recorded_at']) < earliest):
            raise ValueError('R28 per-fixture issue differs: ' + mid)
        rows[mid] = pregame_row(fixture, pair, form, cutoff, saved)
        recorded.append(aware(saved['recorded_at']))
    identity = dict(version=spec['version'], protocol_sha256=sha(PROTOCOL), competitions=[spec['competition']],
                    parent_sha256=parent['record_sha256'], form_sha256=inputs['form_sha256'],
                    source_cutoff_at=max(recorded).isoformat(),
                    pairs={f'xg_same_total_vs_{arm}': [arm, 'xg_same_total']
                           for arm in (*CONTROLS, 'comprehensive')},
                    selected={arm: r28.ranked(rows, arm) for arm in ('xg_same_total', 'goals_same_total')},
                    forecast_sha256={mid: digest(row) for mid, row in rows.items()})
    return dict(identity=identity, rows=rows)


def summary(prepared):
    rows = prepared['rows']
    eligible = {}
    for other in CONTROLS:
        first = members = 0
        for value in rows.values():
            target = value['forecasts']['xg_same_total']
            source = value['forecasts'][other]
            first += target['primary'] != source['primary']
            members += set(target['top3']) != set(source['top3'])
        eligible[other] = dict(first_changes=first, membership_changes=members)
    return dict(fixtures=len(rows), compared=eligible,
                max_total_error=max(r['max_total_projection_error'] for r in rows.values()),
                exposures=sorted({n for r in rows.values() for n in r['exposure']['n']}),
                ready=any(any(v.values()) for v in eligible.values()),
                selected=prepared['identity']['selected'],
                per_match={mid: dict(forecasts=row['forecasts'], exposure=row['exposure']) for mid, row in rows.items()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ('check', 'issue', 'verify'):
        modes.add_argument('--' + mode, type=Path)
    parser.add_argument('--freeze-root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    protocol = r28.read(PROTOCOL)
    prepared = prepare(args.data, protocol)
    result = summary(prepared)
    if args.check:
        write_new(args.check, result)
    else:
        if not result['ready']:
            raise ValueError('fixed-total process scores do not differ on the registered slate')
        if args.issue:
            r42.issue_forward(prepared, args.issue, protocol['track'], args.freeze_root)
        r42.verify_forward(prepared, args.issue or args.verify, protocol['track'])
