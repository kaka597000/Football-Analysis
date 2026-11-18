"""Preregister and freeze three same-cutoff Current/market/cohort score matrices."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit

import numpy as np

RUNTIME = Path(__file__).resolve().parents[1]
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
from automation import market_snapshot as feed
from data_sources import venue
from prediction_engine import file_sha256, predict_models
from publication import atomic_json, deliver
from research.analysis_freeze import aware, digest, save, verify
from storage_lock import exclusive_lock

ARMS = ('current', 'market', 'market_cohort')
BOOK_PRIORITY = ('pinnacle',)
PLAN_TRACK = 'three-arm-plan'
SCORE_TRACK = 'three-arm-score'


def read(path):
    return json.loads(Path(path).read_bytes())


def market_pair(snapshot):
    """Pick one book with an unambiguous WDL and total from the same response."""
    if not snapshot:
        return None
    books = defaultdict(lambda: defaultdict(list))
    for row in snapshot['observations']:
        if row['field'] == 'market_quote':
            value = row['value']
            books[value['bookmaker']][value['market']].append(row)
    for book in sorted(books, key=lambda name: (name not in BOOK_PRIORITY, name)):
        quotes = books[book]
        if len(quotes['h2h']) != 1 or len(quotes['totals']) != 1:
            continue
        h, t = quotes['h2h'][0], quotes['totals'][0]
        if (h['evidence']['sha256'] != t['evidence']['sha256']
                or h['provider_match_id'] != t['provider_match_id']):
            continue
        htime, ttime = h['value']['quoted_at'], t['value']['quoted_at']
        return {'provider': h['provider'], 'bookmaker': book, 'provider_match_id': h['provider_match_id'],
                'match_id': h['match_id'], 'h2h': h['value']['odds'],
                'total': dict(t['value']['odds'], line=t['value']['line']),
                'h2h_quoted_at': htime, 'total_quoted_at': ttime, 'quoted_at': max((htime, ttime), key=aware),
                'captured_at': h['observed_at'], 'source_url': h['evidence']['url'],
                'source_sha256': h['evidence']['sha256'], 'snapshot_sha256': snapshot['snapshot_sha256']}
    return None


def _reference(meta):
    return {'source_url': meta['url'], 'query_time': meta['captured_at'], 'response_sha256': meta['sha256'],
            'source_tier': 'L2', 'supports_field': ['identity', 'format', 'venue']}


def batch_inputs(root, identity_dir, competition, cutoff):
    recorded = read(root / 'market-channel.json')
    meta = read(root / 'source-metadata.json')
    board_meta, odds_meta = meta['identity'], meta['odds']
    board_url, odds_url = urlsplit(board_meta['url']), urlsplit(odds_meta['url'])
    if (board_url.scheme != 'https' or board_url.hostname != 'site.api.espn.com'
            or board_url.path != '/apis/site/v2/sports/soccer/' + competition + '/scoreboard'
            or odds_url.scheme != 'https' or odds_url.hostname != 'api.the-odds-api.com'
            or odds_url.path.rstrip('/') != '/v4/sports/' + feed.SCOPES[competition][0] + '/odds'):
        raise ValueError('retained source endpoint differs from the competition')
    if aware(recorded['created_at']) > aware(cutoff) or recorded.get('retrospective_identity_replay'):
        raise ValueError('market channel unavailable at cutoff')
    def reconstruct(stamp, age):
        return feed.build(root, odds_meta, board_meta, competition=competition, cutoff=stamp,
                          now=aware(stamp), market_max_age_seconds=age)
    if reconstruct(recorded['created_at'], recorded['market_max_age_seconds'])['snapshots'] != recorded['snapshots']:
        raise ValueError('retained market snapshots disagree with original responses')
    current = reconstruct(cutoff, min(3600, recorded['market_max_age_seconds']))
    snapshots = {s['target']['match_id']: s for s in current['snapshots']}
    board = feed.check_evidence(root, board_meta)
    eligible, _ = feed.scheduled_targets(board, competition, aware(cutoff))
    fixtures, markets = [], {}
    for event, sides in eligible:
        mid = 'espn:' + event['id']
        cohort = competition
        if competition == 'uefa.nations':
            match = re.fullmatch(r'Group ([ABCD])[1-4]', event['competitions'][0].get('group', {}).get('name', ''), re.I)
            if not match:
                continue
            cohort += ':' + match[1].upper() + ':league-phase'
        f = {'match_id': mid, 'competition': competition, 'cohort': cohort, 'stage': feed.SCOPES[competition][2],
             'home': 'espn:' + sides['home']['id'], 'away': 'espn:' + sides['away']['id'],
             'home_name': sides['home']['displayName'], 'away_name': sides['away']['displayName'],
             'age': 'senior', 'gender': 'men', 'score_basis': 'regulation_90', 'phase': 'PreMatch',
             'identity_verified': True, 'kickoff_at': event['date'], 'cutoff_at': cutoff,
             'sources': [_reference(board_meta)]}
        neutral = event['competitions'][0].get('neutralSite')
        summary_meta = identity_dir / (event['id'] + '.meta.json') if identity_dir else None
        if summary_meta and summary_meta.is_file():
            ref = read(summary_meta)
            expected = '/apis/site/v2/sports/soccer/' + competition + '/summary'
            endpoint = urlsplit(ref['url'])
            if (endpoint.scheme != 'https' or endpoint.hostname != 'site.api.espn.com' or endpoint.path != expected or
                    parse_qs(urlsplit(ref['url']).query).get('event') != [event['id']]):
                raise ValueError('summary endpoint identity mismatch')
            doc = feed.check_evidence(identity_dir, ref)
            header = doc['header']
            verified = venue.resolve(f, [dict(ref, query_time=ref['captured_at'])], identity_dir)
            status = header['competitions'][0]['status']['type']
            if status['state'] != 'pre' or status['completed'] is not False:
                raise ValueError('summary is not prematch')
            if aware(ref['captured_at']) > aware(cutoff):
                raise ValueError('summary after cutoff')
            if verified['status'] == 'VERIFIED':
                if type(neutral) is bool and neutral != verified['neutral']:
                    raise ValueError('conflicting explicit venue state')
                neutral = verified['neutral']
            f['sources'].append(_reference(ref))
        f['neutral'] = neutral if type(neutral) is bool else None
        fixtures.append(f)
        markets[mid] = market_pair(snapshots.get(mid))
    return sorted(fixtures, key=lambda f: (aware(f['kickoff_at']), f['match_id'])), markets, meta


def total_quote(market):
    return {'match_id': market['match_id'], 'verified': True, 'provider': market['provider'],
            'market': 'standard_total', 'bookmaker': market['bookmaker'], 'line': market['total']['line'],
            'over_odds': market['total']['over'], 'under_odds': market['total']['under'],
            'quoted_at': market['total_quoted_at'], 'query_time': market['captured_at'],
            'source_url': market['source_url'], 'source_sha256': market['source_sha256']}


def forecast(fixture, market, artifact, registry):
    from research_program import three_arm_model as model
    if fixture['neutral'] is None:
        return {'status': 'ABSTAIN_NO_VERIFIED_VENUE', 'arms': {}}
    input_fixture = dict(fixture)
    if market:
        input_fixture['total_quote'] = total_quote(market)
    try:
        state = predict_models(input_fixture, registry, retain_current_on_test_failure=True)
    except (ValueError, FileNotFoundError) as error:
        return {'status': 'ABSTAIN_CURRENT_UNAVAILABLE', 'reason': str(error), 'arms': {}}
    current = state['current']
    matrix = current['matrix']
    if digest(matrix) != current['matrix_hash']:
        raise ValueError('invalid native Current matrix')
    estimates = model.apply(artifact, matrix, market, cohort=fixture['cohort'], cutoff_at=fixture['cutoff_at'])
    if set(estimates) != set(ARMS) or not np.array_equal(matrix, estimates['current']):
        raise ValueError('three-arm output must retain the exact active Current matrix')
    arms = {}
    for role in ARMS:
        probs = estimates[role]
        picks = model.score_picks(probs, line=market['total']['line'] if market else None)
        values = probs.tolist()
        arms[role] = dict(picks, matrix=values, matrix_sha256=digest(values))
    return {'status': 'FROZEN_WITH_MARKET' if market else 'FROZEN_WITHOUT_MARKET',
            'native_current': {'version': current['version'], 'artifact_sha256': state['route']['artifact_sha256'],
                               'engine_sha256': file_sha256(state['current_engine_path']),
                               'matrix_sha256': current['matrix_hash'], 'primary': current['primary'],
                               'wdl': current['wdl_reference'], 'total': current.get('total')},
            'arms': arms}


def first_path(out, mid, track):
    scope = {'match_id': mid, 'stage': 'prematch', 'track': track}
    return out / 'records' / digest(scope) / '000001.json'


def copy_first(out, alias, fixture, track, payload=None):
    path = first_path(out, fixture['match_id'], track)
    if not path.is_file():
        if payload is None:
            raise ValueError('original first freeze is missing')
        save(payload, fixture, stage='prematch', track=track, root=out / 'records',
             idempotency_key='first:' + track)
    record = verify(read(path))
    if (record['fixture'] != fixture or record['track'] != track or record['revision'] != 1
            or record['stage'] != 'prematch' or aware(record['recorded_at']) >= aware(fixture['kickoff_at'])):
        raise ValueError('first freeze identity mismatch')
    alias.parent.mkdir(parents=True, exist_ok=True)
    deliver(alias, path.read_bytes())
    return record


def verify_registration(protocol, fixtures, cutoff):
    if (protocol['schema'] != 'THREE-ARM-RESEARCH-REGISTRATION-1'
            or protocol['automatic_promotion'] is not False or protocol['arms'] != list(ARMS)
            or aware(protocol['recorded_at']) > aware(cutoff)):
        raise ValueError('invalid or late trial registration')
    enrolled = {row['match_id']: row for row in protocol['fixtures']}
    if len(enrolled) != len(protocol['fixtures']) or set(enrolled) != {row['match_id'] for row in fixtures}:
        raise ValueError('capture population differs from registration')
    for fixture in fixtures:
        row = enrolled[fixture['match_id']]
        if (any(row[k] != fixture[k] for k in ('home', 'away', 'cohort', 'stage', 'neutral'))
                or aware(row['kickoff_at']) != aware(fixture['kickoff_at'])
                or row['identity_sha256'] not in [s['response_sha256'] for s in fixture['sources']]):
            raise ValueError('capture fixture identity differs from registration: ' + fixture['match_id'])


def capture(market_dir, competition, artifact_path, registry, out, *, registration, identity_dir=None):
    from research_program import three_arm_model as model
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(out / '.capture.lock'):
        artifact = read(artifact_path)
        binding = {'competition': competition, 'registration_sha256': file_sha256(registration), 'artifact_sha256': file_sha256(artifact_path),
                   'registry_sha256': file_sha256(registry), 'channel_sha256': file_sha256(Path(market_dir) / 'market-channel.json'),
                   'market_builder_sha256': file_sha256(feed.__file__), 'model_source_sha256': file_sha256(model.__file__), 'capture_source_sha256': file_sha256(__file__)}
        mid = 'three-arm-plan:' + competition + ':' + binding['channel_sha256']
        original = first_path(out, mid, PLAN_TRACK)
        plan_path = out / 'plan.json'
        if original.exists() or plan_path.exists():
            existing = verify(read(plan_path if plan_path.is_file() else original))
            if existing['payload']['binding'] != binding:
                raise ValueError('batch source or candidate changed')
            plan = copy_first(out, plan_path, existing['fixture'], PLAN_TRACK)
        else:
            cutoff = datetime.now(timezone.utc).isoformat()
            if aware(artifact['cutoff_at']) > aware(cutoff):
                raise ValueError('calibration unavailable at prediction cutoff')
            fixtures, markets, evidence = batch_inputs(Path(market_dir), Path(identity_dir) if identity_dir else None,
                                                      competition, cutoff)
            if not fixtures:
                raise ValueError('no upcoming identified fixtures in this channel')
            protocol = read(registration)
            verify_registration(protocol, fixtures, cutoff)
            population = {'match_id': mid, 'cutoff_at': cutoff, 'kickoff_at': fixtures[0]['kickoff_at']}
            payload = {'schema': 'WCPP-THREE-ARM-PLAN-1', 'registration_sha256': binding['registration_sha256'],
                       'registration_recorded_at': protocol['recorded_at'], 'binding': binding, 'fixtures': fixtures,
                       'artifact_sha256': binding['artifact_sha256'], 'markets': markets, 'sources': evidence,
                       'arms': list(ARMS), 'book_policy': 'PINNACLE_THEN_LEXICAL_UNIQUE_H2H_AND_TOTAL_SAME_RESPONSE',
                       'primary_policy': 'MATRIX_MODE', 'wdl_policy': 'MARGINAL_MODE',
                       'totals_policy': 'STRICT_FULL_WIN_AMONG_NON_PUSH', 'automatic_promotion': False}
            plan = copy_first(out, plan_path, population, PLAN_TRACK, payload)
        rows = []
        for f in plan['payload']['fixtures']:
            match_id = f['match_id']
            quote = plan['payload']['markets'][match_id]
            path = out / 'freezes' / (match_id.replace(':', '-') + '.json')
            original = first_path(out, match_id, SCORE_TRACK)
            payload = None
            if not original.is_file():
                if aware(f['kickoff_at']) <= datetime.now(timezone.utc):
                    rows.append({'match_id': match_id, 'status': 'MISSED_PREMATCH'})
                    continue
                payload = forecast(f, quote, artifact, registry)
                payload.update(plan_sha256=plan['record_sha256'], market=quote, artifact_sha256=binding['artifact_sha256'])
            record = copy_first(out, path, f, SCORE_TRACK, payload)
            if record['payload']['plan_sha256'] != plan['record_sha256']:
                raise ValueError('first freeze belongs to a different plan')
            rows.append({'match_id': match_id, 'status': record['payload']['status'], 'file': str(path.relative_to(out)),
                         'record_sha256': record['record_sha256'], 'recorded_at': record['recorded_at'],
                         'arms': {k: {key: v[key] for key in ('primary', 'wdl', 'total')}
                                  for k, v in record['payload']['arms'].items()}})
        result = {'schema': 'WCPP-THREE-ARM-BATCH-1', 'population': len(rows), 'cutoff_at': plan['fixture']['cutoff_at'],
                  'plan_sha256': plan['record_sha256'], 'matches': rows, 'automatic_promotion': False}
        atomic_json(out / 'summary.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('market-dir', 'competition', 'artifact', 'registry', 'registration', 'out'):
        parser.add_argument('--' + arg, required=True)
    parser.add_argument('--identity-dir')
    a = parser.parse_args()
    result = capture(a.market_dir, a.competition, a.artifact, a.registry, a.out, registration=a.registration, identity_dir=a.identity_dir)
    print(json.dumps({'population': result['population'], 'cutoff_at': result['cutoff_at'],
                      'statuses': [r['status'] for r in result['matches']]}))


if __name__ == '__main__':
    main()
