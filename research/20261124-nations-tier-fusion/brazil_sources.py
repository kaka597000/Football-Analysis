"""Replay the six registered Brazilian first issues against their same-clock price source."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit

from research.analysis_freeze import aware, digest, verify
from research_program.market_baseline import quote_only_matrices
from research_program.replay import _finished_espn_label
from research_program.three_arm_forward import ARMS, batch_inputs
from research_program.three_arm_model import score_picks

ISSUE = 'aa565df134f05dd9e5c429b83c16b8cf109dc80d'
RESULT = '69821e4cb6db23ac51cc81159ed4a1a0058ca39e'
FIRST = 'workspace-updates/20261118-three-arm/batch/'
MARKET = 'market/runs/44223152835-1-bra1/'
CHOICES_SHA = '5aa46d7bf5eb402fcf96bd5c20b8052e8debc89b412907d06c2906fa336dccf2'


def blob(repo, commit, path):
    return subprocess.check_output(['git', '-C', str(repo), 'show', commit + ':' + path])


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def make_choices(repo, work):
    plan = verify(json.loads(blob(repo, ISSUE, FIRST + 'plan.json')))
    cutoff = plan['fixture']['cutoff_at']
    committed = subprocess.check_output(['git', '-C', str(repo), 'show', '-s', '--format=%cI', ISSUE], text=True).strip()
    for name in ('identity.json', 'odds.json', 'source-metadata.json', 'market-channel.json'):
        (work / name).write_bytes(blob(repo, ISSUE, MARKET + name))
    # The issuing program verifies the response hashes and rebuilds both original markets.
    fixtures, pairs, _ = batch_inputs(work, None, 'bra.1', cutoff)
    roster = {f['match_id']: f for f in fixtures}
    registered = sorted(plan['payload']['fixtures'], key=lambda f: f['match_id'])
    if len(registered) != 6 or len({f['match_id'] for f in registered}) != 6:
        raise ValueError('original six-event registration required')
    choices = []
    for original in registered:
        mid = original['match_id']
        body = blob(repo, ISSUE, FIRST + 'freezes/' + mid.replace(':', '-') + '.json')
        issue = verify(json.loads(body))
        f, payload, market = issue['fixture'], issue['payload'], pairs[mid]
        if (issue['evaluation_slot'] != 'FIRST_PREMATCH' or issue['stage'] != 'prematch'
                or payload['status'] != 'FROZEN_WITH_MARKET' or payload['plan_sha256'] != plan['record_sha256']
                or original != f or f['stage'] != 'regular' or not f['identity_verified']):
            raise ValueError('original fixture/first-issue identity mismatch')
        for key in ('match_id', 'home', 'away', 'home_name', 'away_name', 'kickoff_at', 'competition', 'stage'):
            if f[key] != roster[mid][key]:
                raise ValueError('raw scoreboard and issued fixture disagree: ' + key)
        if (market != payload['market'] or market['bookmaker'] != 'pinnacle'
                or market['match_id'] != mid or aware(market['captured_at']) > aware(cutoff)
                or not aware(cutoff) <= aware(issue['recorded_at']) <= aware(committed) < aware(f['kickoff_at'])):
            raise ValueError('market source or first-issue clock mismatch')
        arms = payload['arms']
        for name in ARMS:
            if digest(arms[name]['matrix']) != arms[name]['matrix_sha256']:
                raise ValueError('original arm matrix checksum mismatch')
        _, projected = quote_only_matrices(market['h2h'], market['total'])
        first = dict(current=arms['current']['primary'], market_calibrated=arms['market']['primary'],
                     market_cohort=arms['market_cohort']['primary'],
                     market_only=score_picks(projected, line=market['total']['line'])['primary'])
        choices.append(dict(id=mid, home_id=f['home'], away_id=f['away'], home=f['home_name'], away=f['away_name'],
                            stage=f['stage'], kickoff_at=f['kickoff_at'], common_cutoff_at=cutoff,
                            forecast_recorded_at=issue['recorded_at'], forecast_freeze_sha256=checksum(body),
                            market_capture_at=market['captured_at'], market_quote_at=market['quoted_at'],
                            market_response_sha256=market['source_sha256'], provider_event_id=market['provider_match_id'],
                            first_score=first))
    return ''.join(json.dumps(r, sort_keys=True) + '\n' for r in choices).encode()


def score_locked(repo, lock, work):
    if checksum(lock) != CHOICES_SHA:
        raise ValueError('six input-only choices differ from their pre-scoring lock')
    choices = {r['id']: r for r in map(json.loads, lock.splitlines())}
    # Only this six-event registry is decoded. Other populations are never scored.
    markers = [f'"match_id": "{mid}"'.encode() for mid in choices]
    labels = {}
    for line in blob(repo, RESULT, 'normalized-results.jsonl').splitlines():
        if not any(marker in line for marker in markers):
            continue
        row = json.loads(line)
        mid = row['match_id']
        if mid not in choices or mid in labels:
            raise ValueError('conflicting result identity')
        labels[mid] = row
    if labels.keys() != choices.keys():
        raise ValueError('incomplete certified result panel')
    for mid, choice in choices.items():
        row = labels[mid]
        ref = row['raw_reference']
        if (row['cohort'] != 'bra.1' or row['stage'] != 'regular' or row['score_basis'] != 'regulation_90'
                or (row['home'], row['away']) != (choice['home_id'], choice['away_id'])
                or aware(row['kickoff_at']) != aware(choice['kickoff_at']) or row['source_url'] != ref['source_url']
                or urlsplit(ref['source_url']).query != 'event=' + mid.split(':')[1]
                or not aware(row['kickoff_at']) < aware(ref['captured_at']) == aware(row['known_at'])):
            raise ValueError('result identity or evidence clock mismatch')
        raw = gzip.decompress(blob(repo, RESULT, 'raw/' + ref['sha256'] + '.json.gz'))
        if checksum(raw) != ref['sha256']:
            raise ValueError('result response checksum mismatch')
        path = work / 'summary.json'
        path.write_bytes(raw)
        _finished_espn_label(row, {'url': row['source_url']}, path)
        score = f"{row['home_goals']}-{row['away_goals']}"
        yield dict(id=mid, home=choice['home'], away=choice['away'], cutoff_at=choice['common_cutoff_at'],
                   kickoff_at=choice['kickoff_at'], **choice['first_score'], regulation_score=score,
                   issue_sha256=choice['forecast_freeze_sha256'], result_sha256=ref['sha256'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('choices', 'score'))
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--lock', required=True, type=Path)
    parser.add_argument('--out', type=Path, help='new CSV for the score phase')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if args.phase == 'choices':
            choices = make_choices(args.data, work)
            if checksum(choices) != CHOICES_SHA:
                raise ValueError('replayed sources differ from the original six-event choice lock')
            with args.lock.open('xb') as stream:
                stream.write(choices)
            return
        if not args.out:
            parser.error('--out is required for score')
        rows = list(score_locked(args.data, args.lock.read_bytes(), work))
        with args.out.open('x', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0], lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)


if __name__ == '__main__':
    main()
