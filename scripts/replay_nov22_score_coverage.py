"""Replay one archived slate against its later results. Prints development diagnostics only."""
import argparse
from collections import Counter
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
sys.path[:0] = [str(RUNTIME), str(RUNTIME / 'v5')]

from research.analysis_freeze import aware, verify
from research.score_distribution_audit import audit_prediction, parse_score
from research_program.replay import _finished_espn_label
from research_program.score_coverage_policy import select_scores
from v5.paired_review import review as paired_review

SLATE = '87e3ad5ba551714dbf840bae84335d3bf619a50a:workspace-updates/20261122-covered-slate'
REVIEW = '22024b23f93e7809aed4d90b027d78ff4e53875c:workspace-updates/20261123-nov22-review-nonmls'
ROLES = ('current_model', 'test_model')


def blob(repo, name):
    return subprocess.check_output(['git', '-C', str(repo), 'show', name])


def members(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for entry in archive:
            if entry.isfile():
                yield entry.name, archive.extractfile(entry).read()


def checked(data, sha):
    if hashlib.sha256(data).hexdigest() != sha:
        raise ValueError(f'archive checksum mismatch: {sha}')
    return data


def published_table(markdown):
    section = markdown.decode().split('## 全部场次\n')[1].split('\n## ')[0]
    scores = {}
    for line in section.splitlines():
        if line.startswith('| 11-'):
            _, _, teams, primary, backups, *_ = (cell.strip() for cell in line.strip('|').split('|'))
            key = tuple(teams.split(' — '))
            if key in scores:
                raise ValueError(f'duplicate published fixture: {teams}')
            scores[key] = [primary.strip('*'), *backups.split('、')]
    return scores


def frozen_inputs(repo):
    checks = json.loads(blob(repo, SLATE + '/verification.json'))['files_sha256']
    published = published_table(checked(blob(repo, SLATE + '/README.md'), checks['README.md']))
    stream = members(checked(blob(repo, SLATE + '/evidence.tar.gz'), checks['evidence.tar.gz']))
    name, manifest = next(stream)
    if name != 'manifest.json':
        raise ValueError('missing forecast archive index')
    files = json.loads(manifest)['files']
    wanted = {files[k]: k for k in files if k.startswith('freeze-copies/')}
    records = {wanted[n[8:]]: verify(json.loads(checked(data, n[8:])))
               for n, data in stream if n.startswith('objects/') and n[8:] in wanted}
    batch = records['freeze-copies/top3.json']
    ranked = batch['payload']['top3']
    scope = batch['payload']['scope_match_ids']
    if len(scope) != len(set(scope)) or len(scope) != len(published):
        raise ValueError('incomplete original slate')
    frozen, excluded = {}, []
    for mid in scope:
        eid = mid.removeprefix('espn:')
        comp = records[f'freeze-copies/{eid}-comprehensive.json']
        pair = records[f'freeze-copies/{eid}-pair.json']
        fixture, payload = comp['fixture'], comp['payload']
        row = payload['user_facing_row']
        displayed = published[tuple(row['teams'])]
        if (comp['match_id'] != mid or comp['evaluation_slot'] != 'FIRST_PREMATCH'
                or comp['track'] != 'comprehensive' or comp['stage'] != 'prematch'
                or displayed != [row['first_score'], *row['alternates']]
                or displayed != [payload['primary'], *payload['alternates']]
                or len(displayed) != 3 or len(set(displayed)) != 3):
            raise ValueError(f'first published scores conflict: {mid}')
        if fixture['cohort'] == 'usa.1':
            excluded.append(mid)
            continue
        reference = payload['current_test_reference']
        pf = pair['fixture']
        cutoff, kickoff = aware(fixture['cutoff_at']), aware(fixture['kickoff_at'])
        if (pair['record_sha256'] != reference['sha256'] or pair['freeze_id'] != reference['freeze_id']
                or pair['track'] != 'current-test-pair' or pair['stage'] != 'prematch'
                or any(pf[k] != fixture[k] for k in ('match_id', 'cohort', 'stage', 'home', 'away', 'kickoff_at'))
                or not aware(pf['cutoff_at']) <= aware(pair['recorded_at']) <= cutoff
                or not cutoff <= aware(comp['recorded_at']) < kickoff):
            raise ValueError(f'unpaired prematch evidence: {mid}')
        forecasts = [pair['payload'][r] for r in ROLES]
        for forecast in forecasts:
            if (forecast['input'] != pf or forecast['training_data_cutoff'][:10] >= pf['cutoff_at'][:10]
                    or not aware(pf['cutoff_at']) <= aware(forecast['generated_at']) <= cutoff):
                raise ValueError(f'model chronology or inputs conflict: {mid}')
        # One call returns both selections on exactly the same PMFs, quote, primary and cutoff.
        policy = select_scores([f['matrix'] for f in forecasts], primary=displayed[0],
                               fixture=fixture, policy='diverse')
        frozen[eid] = dict(fixture=fixture, comprehensive_sha=comp['record_sha256'], pair=pair,
                           displayed=displayed, compared=payload['selection_evidence']['compared_scores'],
                           probability=policy['reference_scores'], diverse=[policy['primary'], *policy['alternates']],
                           verified_line=policy['verified_total_line'])
    if (batch['track'] != 'top3-selection' or batch['evaluation_slot'] != 'FIRST_PREMATCH'
            or [r['rank'] for r in ranked] != [1, 2, 3] or len({r['match_id'] for r in ranked}) != 3
            or aware(batch['recorded_at']) >= min(aware(v['fixture']['kickoff_at']) for v in frozen.values())):
        raise ValueError('invalid first ranked batch')
    for r in ranked:
        v = frozen[r['match_id'].removeprefix('espn:')]
        if (r['comprehensive_reference']['sha256'] != v['comprehensive_sha']
                or [r['primary'], *r['alternates']] != v['displayed']):
            raise ValueError('ranked match does not reference its published first version')
    return frozen, excluded, [r['match_id'].removeprefix('espn:') for r in ranked]


def verified_results(repo, frozen):
    validation = json.loads(blob(repo, REVIEW + '/verification.json'))
    archive = checked(blob(repo, REVIEW + '/evidence.tar.gz'), validation['sha256']['evidence.tar.gz'])
    entries = dict(members(archive))
    results = json.loads(entries['verified-results.json'])
    lock = json.loads(entries['population-lock.json'])['population']
    if set(frozen) != set(results) or set(frozen) != set(lock):
        raise ValueError('forecast and settlement populations differ')
    with TemporaryDirectory() as directory:
        espn_file = Path(directory) / 'summary.json'
        for eid, result in results.items():
            row, fixture = lock[eid], frozen[eid]['fixture']
            refs = {s['source']: s for s in result['sources']}
            espn, fot = refs['espn'], refs['fotmob']
            espn_file.write_bytes(checked(entries[espn['file']], espn['sha256']))
            # Both providers recorded a later kickoff for KFUM–Brann. Keep the forecast immutable.
            final_kickoff = result['observed_provider_kickoff_at']
            _finished_espn_label(dict(result, kickoff_at=final_kickoff), espn, espn_file)
            fm = json.loads(checked(entries[fot['file']], fot['sha256']))
            teams, status = fm['header']['teams'], fm['header']['status']
            if (str(fm['general']['matchId']) != str(fixture['fotmob_fixture_id'])
                    or [str(t['id']) for t in teams] != [row['fot_home'], row['fot_away']]
                    or status['reason']['short'] != 'FT' or not status['finished']
                    or status.get('awarded') or status.get('whoLostOnPenalties')
                    or aware(status['utcTime']) != aware(final_kickoff)
                    or aware(fixture['kickoff_at']) > aware(final_kickoff)
                    or min(aware(v['observed_at']) for v in refs.values()) < aware(final_kickoff) + timedelta(minutes=90)
                    or [t['score'] for t in teams] != [result['home_goals'], result['away_goals']]):
                raise ValueError(f'archived regulation result or identity conflict: {eid}')
    return results


def transitions(rows, before, after, slots):
    gained, lost = [], []
    for eid, row in rows.items():
        old = row['actual'] in row[before][:slots]
        new = row['actual'] in row[after][:slots]
        if new and not old:
            gained.append(eid)
        if old and not new:
            lost.append(eid)
    return {'gained': gained, 'lost': lost, 'net': len(gained) - len(lost)}


def replay(repo):
    frozen, excluded, ranked = frozen_inputs(repo)
    results = verified_results(repo, frozen)
    pairs = [dict(v['pair']['payload'], fixture=v['pair']['fixture']) for v in frozen.values()]
    reviewed = paired_review(pairs, list(results.values()))
    rows = {}
    for eid, v in frozen.items():
        result = results[eid]
        actual = f"{result['home_goals']}-{result['away_goals']}"
        cap = max(sum(parse_score(s)) for s in v['displayed'])
        row = {k: v[k] for k in ('displayed', 'probability', 'diverse', 'verified_line')}
        row.update(actual=actual, cap=cap, above=sum(parse_score(actual)) > cap,
                   in_compared=actual in v['compared'], pair_sha=v['pair']['record_sha256'],
                   comprehensive_sha=v['comprehensive_sha'], cutoff=v['fixture']['cutoff_at'],
                   model_cutoff=v['pair']['fixture']['cutoff_at'], cohort=v['fixture']['cohort'])
        for role in ROLES:
            forecast = v['pair']['payload'][role]
            # Inspect the realized cell only after both policies have produced their three scores.
            audit = audit_prediction(forecast, actual)
            row[role] = [x['score'] for x in forecast['top3']]
            if row[role][0] != forecast['primary'] or len(set(row[role])) != 3 or len(row[role]) != 3:
                raise ValueError(f'inconsistent frozen matrix choices: {eid}/{role}')
            row[role + '_version'] = forecast['version']
            row[role + '_training_end'] = forecast['training_data_cutoff']
            row[role + '_tail'] = sum(audit['total_probabilities'][cap + 1:])
            row[role + '_four_plus'] = audit['p_total_ge_4']
            row[role + '_actual_rank'] = audit['comprehensive_selection']['probability_rank']
        rows[eid] = row
    arms = ('displayed', *ROLES, 'probability', 'diverse')
    hits = {a: [sum(r['actual'] in r[a][:k] for r in rows.values()) for k in (1, 3)] for a in arms}
    above = [e for e, r in rows.items() if r['above']]
    contrasts = [('probability', 'diverse'), ('displayed', 'diverse'), ('current_model', 'test_model'),
                 ('current_model', 'displayed'), ('test_model', 'displayed')]
    changes = {f'{a}_to_{b}': {f'top{k}': transitions(rows, a, b, k) for k in (1, 3)}
               for a, b in contrasts}
    expected = {a: {k: sum(r[f'{a}_{k}'] for r in rows.values()) for k in ('tail', 'four_plus')}
                for a in ROLES}
    return {
        'development_only': True, 'slate': SLATE, 'review': REVIEW, 'n': len(rows), 'excluded': excluded,
        'ranked': [(e, rows[e]['actual'], rows[e]['displayed']) for e in ranked],
        'hits': hits, 'above': above,
        'actual_four_plus': sum(sum(parse_score(r['actual'])) >= 4 for r in rows.values()),
        'expected': expected, 'changes': changes,
        'selector_changed': [e for e, r in rows.items() if r['diverse'] != r['probability']],
        'paired_review_top1': {k: v['primary_hits'] for k, v in reviewed['models'].items()},
        'pair_slots': dict(Counter(v['pair']['evaluation_slot'] for v in frozen.values())), 'rows': rows,
    }


if __name__ == '__main__':
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('data_repo', type=Path, help='local Data repository containing both pinned commits')
    print(json.dumps(replay(args.parse_args().data_repo), ensure_ascii=False, indent=2, allow_nan=False))
