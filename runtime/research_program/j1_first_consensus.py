"""Test agreement on an existing J1 backup as the single first score."""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from research.analysis_freeze import DEFAULT_ROOT, aware, digest
from . import all_scope_model as base
from . import continuation_study as archive
from . import europe_score_dispersion as dispersion
from . import goal_state_forward as ledger
from . import j1_fixed_first_backups as r42
from . import j1_local_score_calibration as r41
from . import j1_process_forward as j1_forward
from . import j1_score_dispersion as r29
from . import j1_team_allocation as j1
from . import r8_score_study as controls
from .goal_state_study import sha, write_new
from .ucl_zero_goals import IDENTITY, reference_rows


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261127-r43-j1-first-consensus/protocol.json'
ARMS = ('consensus', 'dispersion_only', 'local_only')


def promote(original, proposal):
    ordered = original['top3']
    if len(ordered) != 3 or len(set(ordered)) != 3 or ordered[0] != original['primary']:
        raise ValueError('an original first and three distinct R7 scores are required')
    if proposal not in ordered[1:]:
        return original
    return dict(original, primary=proposal, top3=[proposal, *(score for score in ordered if score != proposal)])


def score_choices(original, dispersion_first, local_first):
    ordered_dispersion = promote(original, dispersion_first)
    ordered_local = promote(original, local_first)
    return dict(r7=original, dispersion_only=ordered_dispersion, local_only=ordered_local,
                consensus=ordered_dispersion if dispersion_first == local_first else original)


def locked_archives(data, spec):
    locked = spec['archives']
    if sha(j1.PROTOCOL) != locked['r25_protocol_sha256']:
        raise ValueError('the original development populations changed')
    paths = {}
    for key, source in (('r29', r29.PROTOCOL), ('r41', r41.PROTOCOL), ('forward', r42.PROTOCOL)):
        pin = locked[key]
        if sha(source) != pin['protocol.json']:
            raise ValueError('the prior recipe changed: ' + key)
        root = data / pin['path']
        for relative, expected in pin.items():
            if relative.endswith(('.json', '.gz')) and sha(root / relative) != expected:
                raise ValueError('registered evidence differs: ' + str(root / relative))
        paths[key] = root
    return paths


def paired_rows(paths, file, windows, splits):
    previous = reference_rows(paths['r29'] / 'replay' / file)
    learned = reference_rows(paths['r41'] / 'replay' / file)
    if previous.keys() != learned.keys():
        raise ValueError('the two calibrations must cover the same registered J1 fixtures')
    rows = []
    for mid, left in previous.items():
        right = learned[mid]
        name = left['window']
        if name not in splits:
            raise ValueError('unexpected development window: ' + name)
        bounds = windows[name]
        if (any(left[key] != right[key] for key in (*IDENTITY, 'window'))
                or left['cohort'] != j1.COMPETITION or left['stage'] != j1.STAGE
                or not bounds['from'] <= left['date'] < bounds['before']):
            raise ValueError('calibrations differ on fixture, result, date or actual line: ' + mid)
        r29_scores, r41_scores = left['forecast'], right['forecast']
        if any(r29_scores[arm] != r41_scores[arm] for arm in ('r7', 'market')):
            raise ValueError('calibrations disagree on signed original or market control: ' + mid)
        disp, local = (item['full_dose']['primary'] for item in (r29_scores, r41_scores))
        picks = score_choices(r29_scores['r7'], disp, local)
        rows.append(dict({key: left[key] for key in (*IDENTITY, 'window')},
                         proposed_firsts=dict(r29=disp, r41=local), forecast=dict(picks, market=r29_scores['market'])))
    if Counter(row['window'] for row in rows) != {name: windows[name]['fixtures'] for name in splits}:
        raise ValueError('the original development denominators differ')
    return rows


def summarize(rows):
    summary = r29.summarize(rows, ARMS, ('r7', 'market', 'dispersion_only', 'local_only'))
    counts = summary['scores']['r7']['counts']
    first, three = (counts[key][0] for key in r29.SCORE_METRICS)
    promoted = [row for row in rows if row['forecast']['consensus']['primary'] != row['forecast']['r7']['primary']]
    summary['reordering_ceiling'] = dict(first_hits=first, backup_only_hits=three-first,
                                         attainable_hits_if_outcomes_known=three, outside_three=len(rows)-three)
    summary['agreement_promotions'] = dict(Counter(
        row['forecast']['r7']['primary'] + ' -> ' + row['forecast']['consensus']['primary'] for row in promoted))
    return summary


def forward_inputs(paths, spec):
    folder = paths['forward'] / 'issued'
    refs = controls.read(folder / 'receipts.json')
    plan = ledger._read(folder, refs['registration'])
    fixtures = plan['payload']['fixtures']
    source_cutoff = aware(plan['recorded_at'])
    deadline = min(aware(item['kickoff_at']) for item in fixtures.values())
    if (plan['freeze_id'] != spec['archives']['forward']['registration']
            or len(fixtures) != 10 or set(refs['matches']) != set(fixtures) or source_cutoff >= deadline):
        raise ValueError('expected the complete pregame R42 slate')
    originals, priors, locals_ = [], [], []
    for mid, fixture in fixtures.items():
        saved = ledger._read(folder, refs['matches'][mid])
        row = dict(saved['payload'])
        registration = row.pop('registration_sha256')
        if (saved['track'] != plan['payload']['track'] or row['fixture'] != fixture
                or saved['fixture'] != dict(fixture, cutoff_at=plan['recorded_at'])
                or registration != plan['record_sha256']
                or digest(row) != plan['payload']['identity']['forecast_sha256'][mid]
                or not source_cutoff <= aware(saved['recorded_at']) < deadline):
            raise ValueError('R42 source does not reproduce its original issue: ' + mid)
        prior, local = (np.asarray(row['matrices'][arm], dtype=float) for arm in ('r7', 'full_dose'))
        if any(m.shape != (base.SIZE, base.SIZE) or not np.isfinite(m).all() or np.any(m < 0)
               or abs(m.sum() - 1.) > 2e-12 for m in (prior, local)):
            raise ValueError('invalid retained score distribution on ' + mid)
        original = dict(row['forecasts']['r7'])
        original.pop('first_probability')
        if original != base.pick(prior, row['line']):
            raise ValueError('R42 retained grid differs from the original R7 forecast: ' + mid)
        originals.append((mid, row, original, saved))
        priors.append(prior)
        locals_.append(local)
    return plan, originals, np.array(priors), np.array(locals_)


def prepare_forward(paths, spec):
    parent, originals, priors, locals_ = forward_inputs(paths, spec)
    fit = controls.read(paths['r29'] / 'replay/fit.json')
    beta = tuple(fit['beta'][kind] for kind in ('win', 'draw'))
    adjusted, _ = dispersion.adjust(priors, beta)
    rows = {}
    for (mid, ref, original, saved), prior, local, new in zip(originals, priors, locals_, adjusted, strict=True):
        disp_first, local_first = (base.top_three_scores(matrix)[0] for matrix in (new, local))
        picks = score_choices(original, disp_first, local_first)
        consensus = picks['consensus']
        lead = tuple(map(int, consensus['primary'].split('-')))
        forecasts = dict(r7=original, consensus=dict(consensus, first_probability=float(prior[lead])),
                         market=ref['forecasts']['market'], comprehensive=ref['forecasts']['comprehensive'])
        rows[mid] = dict(fixture=ref['fixture'], line=ref['line'], forecasts=forecasts,
                         proposed_firsts=dict(r29=disp_first, r41=local_first),
                         matrices=dict(r7=prior.tolist(), r29=new.tolist(), r41=local.tolist()),
                         parent_first_sha256=saved['record_sha256'])
    source_cutoff = max(aware(saved['recorded_at']) for _, _, _, saved in originals).isoformat()
    selected = j1_forward.ranked(rows, 'consensus')
    identity = dict(version=spec['version'], competitions=[j1.COMPETITION], protocol_sha256=sha(PROTOCOL),
                    source_cutoff_at=source_cutoff, parent_sha256=parent['record_sha256'], r29_coefficients=fit['beta'],
                    pairs={f'consensus_vs_{arm}': [arm, 'consensus'] for arm in ('r7', 'comprehensive', 'market')},
                    selected={'consensus': selected}, forecast_sha256={mid: digest(row) for mid, row in rows.items()})
    return dict(identity=identity, rows=rows)


def forward_check(prepared):
    rows = []
    for mid, row in prepared['rows'].items():
        original, consensus = (row['forecasts'][arm] for arm in ('r7', 'consensus'))
        rows.append(dict(match_id=mid, kickoff_at=row['fixture']['kickoff_at'], r7=original['top3'],
                         consensus=consensus['top3'], proposed_firsts=row['proposed_firsts'],
                         changed=original['primary'] != consensus['primary']))
    return dict(registered=len(rows), changed_first=sum(row['changed'] for row in rows),
                source_cutoff_at=prepared['identity']['source_cutoff_at'],
                identities_sha256=digest(prepared['identity']), matches=rows)


def replay(paths, spec, out):
    windows = controls.read(j1.PROTOCOL)['windows']
    validation = paired_rows(paths, 'validation.jsonl.gz', windows, ('validation',))
    history = paired_rows(paths, 'forecasts.jsonl.gz', windows, j1.HISTORY)
    if {row['id'] for row in validation} & {row['id'] for row in history}:
        raise ValueError('the previous validation and evaluation samples overlap')
    groups = {name: [row for row in history if row['window'] == name] for name in j1.HISTORY}
    groups.update(validation=validation, combined=history)
    metrics = {name: summarize(rows) for name, rows in groups.items()}
    check = forward_check(prepare_forward(paths, spec))

    out.mkdir(parents=True, exist_ok=False)
    write_new(out / 'summary.json', dict(version=spec['version'], protocol_sha256=sha(PROTOCOL),
                                        windows=metrics))
    archive.put(out / 'validation.jsonl.gz', validation)
    archive.put(out / 'history.jsonl.gz', history)
    write_new(out / 'forward-check.json', check)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    options = parser.add_mutually_exclusive_group(required=True)
    for mode in ('replay', 'issue', 'verify'):
        options.add_argument('--' + mode, type=Path)
    parser.add_argument('--freeze-root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    specification = controls.read(PROTOCOL)
    archives = locked_archives(args.data, specification)
    if args.replay:
        replay(archives, specification, args.replay)
    else:
        prepared = prepare_forward(archives, specification)
        if not forward_check(prepared)['changed_first']:
            raise ValueError('the consensus produces no different first score on this slate')
        if args.issue:
            r42.issue_forward(prepared, args.issue, specification['track'], args.freeze_root)
        r42.verify_forward(prepared, args.issue or args.verify, specification['track'])
