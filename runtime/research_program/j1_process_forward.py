"""Compare the prior, matched goals and measured chance creation on one J1 slate."""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np

from publication import atomic_json
from research.analysis_freeze import DEFAULT_ROOT, aware, digest
from . import goal_state_forward as ledger
from .three_arm_model import _matrix, score_picks, top_three_scores


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r28-j1-process-forward/protocol.json'
REGISTRATION = 'workspace-updates/20261125-j1-stage-windows/registration'
FIRST_BATCH = 'workspace-updates/20261125-j1-stage-windows/nov25'
SECOND_BATCH = 'workspace-updates/20261126-r26-j1-first-stage/nov28_29'
FIRST_RESULTS = 'workspace-updates/20261125-forward-settlement/nov25/fixtures.csv'
PRIOR_MATCHES = 8
RATIO_LIMIT = 1.25
SIDES = ('home', 'away')
WDL = {'主胜': 'home', '平': 'draw', '平局': 'draw', '客胜': 'away'}
PAIRS = {'xg_vs_prior': ['common_prior', 'xg_history'],
         'xg_vs_goals': ['goals_history', 'xg_history'],
         'xg_vs_comprehensive': ['comprehensive', 'xg_history']}


def read(path):
    return json.loads(path.read_bytes())


def verified_form(data, protocol):
    files = protocol['inputs']
    body = (data / files['form']).read_bytes()
    if hashlib.sha256(body).hexdigest() != files['form_sha256']:
        raise ValueError('registered team form evidence differs')
    return json.loads(body)


def original_batch(data, location, name, plan):
    root = data / location
    publication = ledger._read(root, read(root / 'publication.json'))
    rows = publication['payload']['identity']
    slates = plan['payload']['identity']['slates']
    fixtures = plan['payload']['fixtures']
    if rows['parent'] != plan['record_sha256'] or rows['window'] != name or set(rows['rows']) != set(slates[name]):
        raise ValueError('publication differs from the original registered population')
    dataset = {}
    for mid in slates[name]:
        refs = rows['rows'][mid]
        authored, pair = (ledger._read(root, refs[k]) for k in ('authored', 'pair'))
        fixture = fixtures[mid]
        if (authored['track'] != 'comprehensive' or pair['track'] != 'current-test-pair'
                or authored['payload']['current_test_reference']['sha256'] != pair['record_sha256']
                or not aware(pair['recorded_at']) <= aware(authored['recorded_at']) <= aware(publication['recorded_at'])
                or aware(publication['recorded_at']) >= aware(fixture['kickoff_at'])):
            raise ValueError('original Current/Test or comprehensive issue differs for ' + mid)
        for parent in (authored, pair):
            saved = parent['fixture']
            if (any(saved[k] != fixture[k] for k in ('match_id', 'cohort', 'home', 'away'))
                    or saved['stage'] != 'regular'
                    or aware(saved['kickoff_at']) != aware(fixture['kickoff_at'])):
                raise ValueError('original issue and fixture identities differ for ' + mid)
        dataset[mid] = (fixture, authored, pair)
    return publication, dataset


def native_matrices(pair):
    matrices = tuple(_matrix(pair['payload'][model]['matrix']) for model in ('current_model', 'test_model'))
    if {m.shape for m in matrices} != {(51, 51)}:
        raise ValueError('registered native J1 matrices require aligned score support')
    return matrices


def matched_observations(attacker, defender, cutoff):
    """Use the same observed games for xG and goals, deduplicated by scoring side."""
    observations = {}
    for series, index in ((attacker, 0), (defender, 1)):
        for game in series['fixtures']:
            if aware(game['kickoff_at']) >= cutoff:
                raise ValueError('ineligible form after the process cutoff')
            if game.get('process_capture') and aware(game['process_capture']['observed_at']) > cutoff:
                raise ValueError('form process was acquired after the process cutoff')
            if None in game['xg']:
                continue
            scoring_side = SIDES[(SIDES.index(game['side']) + index) % 2]
            key = game['match_id'], scoring_side
            value = game['goals'][index], game['xg'][index]
            if observations.setdefault(key, value) != value:
                raise ValueError('conflicting history for the same match and scoring team')
    return list(observations.values())


def process_arms(prior, histories):
    home, away = np.indices(prior.shape)
    rates = np.array([(prior * coordinate).sum() for coordinate in (home, away)])
    if np.any(rates <= 0.):
        raise ValueError('positive common-prior goal rates required')
    sizes = np.array([len(rows) for rows in histories])
    sums = np.array([np.array(rows).reshape(-1, 2).sum(axis=0) for rows in histories])
    shrunk = (sums + PRIOR_MATCHES * rates[:, None]) / (sizes[:, None] + PRIOR_MATCHES)
    ratios = np.clip(shrunk / rates[:, None], 1. / RATIO_LIMIT, RATIO_LIMIT)
    result = {}
    for arm, (h_ratio, a_ratio) in zip(('goals_history', 'xg_history'), ratios.T, strict=True):
        adjusted = prior * np.exp(home * np.log(h_ratio) + away * np.log(a_ratio))
        result[arm] = adjusted / adjusted.sum()
    return result, dict(n=sizes.tolist(), prior_rates=rates.round(12).tolist(),
                        shrunk=shrunk.round(12).tolist(), ratios=ratios.round(12).tolist())


def attempt(fixture, authored, pair, form, cutoff):
    current, test = native_matrices(pair)
    common = np.sqrt(current) * np.sqrt(test)
    mass = common.sum()
    if mass <= 0.:
        raise ValueError('native matrices do not overlap')
    prior = common / mass
    team = [form['teams'][fixture[s]] for s in SIDES]
    histories = [matched_observations(team[0], team[1], cutoff), matched_observations(team[1], team[0], cutoff)]
    adjusted, exposure = process_arms(prior, histories)
    wdl = score_picks(prior)['wdl']
    original = authored['payload']
    forecasts = {'comprehensive': dict(primary=original['primary'], top3=original['displayed_scores'],
                                       wdl=WDL[original['wdl']])}
    for arm, matrix in dict(common_prior=prior, **adjusted).items():
        top3 = top_three_scores(matrix)
        forecasts[arm] = dict(primary=top3[0], top3=top3, wdl=wdl,
                              first_probability=round(float(matrix.max()), 12))
    return dict(fixture=fixture, forecasts=forecasts, exposure=exposure, line=None,
                source_pair_sha256=pair['record_sha256'], source_comprehensive_sha256=authored['record_sha256'])


def ranked(rows, arm):
    return sorted(rows, key=lambda mid: (-rows[mid]['forecasts'][arm]['first_probability'],
                                         aware(rows[mid]['fixture']['kickoff_at']), mid))[:3]


def diagnosis(data, plan, form):
    _, batch = original_batch(data, FIRST_BATCH, 'nov25', plan)
    with (data / FIRST_RESULTS).open(encoding='utf-8', newline='') as stream:
        settled = {r['id']: r for r in csv.DictReader(stream) if r['competition'] == 'J1'}
    if len(batch) != 7 or set(batch) != set(settled) or set(batch) != set(form['match_process']):
        raise ValueError('seven original firsts and independently checked results required')
    result = {}
    for mid, (_, issued, pair) in batch.items():
        source = settled[mid]
        actual = source['regulation_score']
        process = form['match_process'][mid]
        h, a = process['goals']
        scores = issued['payload']['displayed_scores']
        if (source['first_sha256'] != issued['record_sha256'] or source['native_pair_sha256'] != pair['record_sha256']
                or [source[k] for k in ('first', 'second', 'third')] != scores or actual != f'{h}-{a}'):
            raise ValueError('incorrect post-match attachment for ' + mid)
        native = {}
        for key, matrix in zip(('current', 'test'), native_matrices(pair), strict=True):
            ranks = np.argsort(-matrix.ravel(), kind='stable')
            flat = np.ravel_multi_index((h, a), matrix.shape)
            native[key] = dict(primary=pair['payload'][key + '_model']['primary'],
                               actual_rank=1 + int(np.flatnonzero(ranks == flat)[0]))
        result[mid] = dict(home=source['home'], away=source['away'], original_scores=scores, actual=actual,
                           displayed_rank=scores.index(actual) + 1 if actual in scores else None,
                           native=native, postmatch_xg=process['xg'], postmatch_shots_on_target=process['shots_on_target'])
    return result


def prepare(data):
    protocol = read(PROTOCOL)
    directory = data / REGISTRATION
    plan = ledger._read(directory, read(directory / 'plan.json'))
    publication, original = original_batch(data, SECOND_BATCH, 'nov28_29', plan)
    locked = protocol['inputs']
    if (plan['freeze_id'] != locked['original_registration'] or
            publication['record_sha256'] != locked['original_publication_sha256'] or len(original) != 10):
        raise ValueError('unexpected parent or publication for the research slate')
    form = verified_form(data, protocol)
    cutoff = aware(form['observed_at'])
    earliest = min(aware(f['kickoff_at']) for f, _, _ in original.values())
    if not aware(publication['recorded_at']) < cutoff < earliest:
        raise ValueError('post-publication form must precede the first kickoff')
    rows = {mid: attempt(*record, form, cutoff) for mid, record in original.items()}
    selected = {arm: ranked(rows, arm) for arm in ('common_prior', 'goals_history', 'xg_history')}
    identity = dict(version=protocol['version'], competitions=[protocol['competition']], pairs=PAIRS,
                    protocol_sha256=hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(), form_sha256=locked['form_sha256'],
                    replay_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    process_cutoff_at=form['observed_at'], parent_sha256=plan['record_sha256'],
                    original_publication_sha256=publication['record_sha256'], selected=selected,
                    forecast_sha256={mid: digest(row) for mid, row in rows.items()})
    return dict(identity=identity, rows=rows, diagnosis_11_25=diagnosis(data, plan, form))


def issue(result, out, freeze_root):
    track = read(PROTOCOL)['track']
    identity, rows = result['identity'], result['rows']
    earliest = min(aware(row['fixture']['kickoff_at']) for row in rows.values())
    if not aware(identity['process_cutoff_at']) <= datetime.now(timezone.utc) < earliest:
        raise ValueError('original slate is outside the full-window prematch issue interval')
    plan = ledger.register([r['fixture'] for r in rows.values()], identity, track, out, freeze_root=freeze_root)
    versions = {mid: ledger.issue(plan, dict(row['fixture'], cutoff_at=plan['recorded_at']), row, track, out,
                                  freeze_root=freeze_root) for mid, row in rows.items()}
    index = dict(registration=plan, matches=versions)
    if (out / 'receipts.json').exists() and read(out / 'receipts.json') != index:
        raise ValueError('existing research receipts differ from original records')
    atomic_json(out / 'receipts.json', index)


def verify_issue(result, out):
    index = read(out / 'receipts.json')
    plan = ledger._read(out, index['registration'])
    track = read(PROTOCOL)['track']
    if plan['payload']['identity'] != result['identity'] or plan['track'] != track + '-registration':
        raise ValueError('replay differs from original research registration')
    rows = result['rows']
    if set(index['matches']) != set(rows) or plan['payload']['fixtures'] != {m: row['fixture'] for m, row in rows.items()}:
        raise ValueError('replay differs from original research fixture list')
    deadline = min(aware(r['fixture']['kickoff_at']) for r in rows.values())
    if not aware(result['identity']['process_cutoff_at']) <= aware(plan['recorded_at']) < deadline:
        raise ValueError('research registration falls outside the admissible window')
    for mid, reference in index['matches'].items():
        record = ledger._read(out, reference)
        payload = dict(rows[mid], registration_sha256=plan['record_sha256'])
        if (record['payload'] != payload or record['track'] != track
                or record['fixture'] != dict(rows[mid]['fixture'], cutoff_at=plan['recorded_at'])
                or not aware(plan['recorded_at']) <= aware(record['recorded_at']) < deadline):
            raise ValueError('replay differs from original prematch research issue: ' + mid)
    pending = ledger.review(out, [], out, by_competition=True)['jpn.1']
    if pending['registered'] != 10 or pending['settled'] != 0:
        raise ValueError('unexpected population in empty-result readiness check')
    return dict(registration=plan['freeze_id'], registered=pending['registered'], pending=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--issue', type=Path)
    action.add_argument('--verify', type=Path)
    parser.add_argument('--freeze-root', type=Path, default=Path(DEFAULT_ROOT))
    args = parser.parse_args()
    report = prepare(args.data)
    if args.issue:
        issue(report, args.issue, args.freeze_root)
    if args.verify or args.issue:
        print(json.dumps(verify_issue(report, args.verify or args.issue), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
