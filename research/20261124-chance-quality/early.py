"""Replay the Nov 4 European Cup opportunity inputs without opening target results."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from opportunities import check_source, clock, matrix, native_pair, recent_three, require, shot_profile, side_stat

INDEX_SHA = '0545bd1cf8686229f1cc6e2f40f0db308c44b98f31c1d69e37acbef5f42f1db1'
TARGETS = {'401915301','401915302','401915303','401915304','401915305','401915306',
           '401915381','401915382','401915383'}


def read_index(directory):
    raw = (directory / 'ucl-evidence/recentxg_injury_rest.json').read_bytes()
    require(sha256(raw).hexdigest() == INDEX_SHA, 'source_index_digest')
    index = json.loads(raw)
    require({str(row['espn_event_id']) for row in index['target_matches']} == TARGETS
            and len(index['target_matches']) == len(TARGETS), 'target_roster')
    receipts = {Path(row['raw_file']).name: row for row in index['source_queries']}
    require(len(receipts) == len(index['source_queries']), 'receipt_conflict')
    return index, receipts


def attempt_fixture(directory, crosswalk, receipts, completed_at):
    mid = str(crosswalk['espn_event_id'])
    f = json.loads((directory / 'fixtures' / (mid + '.json')).read_bytes())
    cutoff, kickoff = clock(f['cutoff_at']), clock(f['kickoff_at'])
    tid = str(crosswalk['fotmob_match_id'])
    native_ids = tuple(str(crosswalk[side + '_fotmob_team_id']) for side in ('home','away'))
    provider_ids = tuple('espn:' + str(crosswalk['espn_home_away_ids'][side]['id']) for side in ('home','away'))
    require(f['identity_verified'] and f['match_id'] == 'espn:'+mid and f['age'] == 'senior'
            and f['cohort'] == 'uefa.champions:NEW_FORMAT:league_phase' and f['phase'] == 'PreMatch'
            and provider_ids == (f['home'], f['away']) and clock(crosswalk['utc']) == kickoff
            and completed_at <= cutoff < kickoff, 'original_fixture_or_cutoff')

    def source(kind, native_id):
        name = f'{kind}-{native_id}.json'
        require(name in receipts, 'no_raw_receipt')
        return check_source(receipts[name], directory / 'ucl-evidence/raw', kind, native_id, cutoff, completed_at)

    target = source('matchDetails', tid)
    g = target['general']
    require(str(g['matchId']) == tid and native_pair(target) == native_ids
            and g['parentLeagueId'] == 42 and g['leagueName'] == 'Champions League' and not g['started']
            and clock(g['matchTimeUTCDate']) == kickoff, 'native_target_identity')

    histories, slots = [], []
    for team in native_ids:
        schedule = recent_three(source('teams', team), team, tid, native_ids, kickoff, cutoff)
        oriented = []
        for rank in range(3):
            item = {'team': team, 'rank': rank+1, 'state': 'calendar_short'}
            if rank < len(schedule):
                prior = schedule[rank]
                rid = str(prior['id'])
                item['prior'] = rid
                try:
                    raw = source('matchDetails', rid)
                    header_ids = native_pair(raw)
                    require(team in header_ids and header_ids == (str(prior['home']['id']), str(prior['away']['id']))
                            and str(raw['general']['matchId']) == rid
                            and clock(raw['general']['matchTimeUTCDate']) == clock(prior['status']['utcTime']),
                            'prior_identity')
                    require(prior['status'].get('reason', {}).get('short') == 'FT'
                            and raw['header']['status'].get('reason', {}).get('short') == 'FT',
                            'no_completed_regulation')
                    native = raw.get('content', {}).get('stats') or {}
                    for name in ('shots_inside_box', 'big_chance', 'expected_goals_non_penalty'):
                        value = side_stat(raw, name) if 'All' in native.get('Periods', {}) else None
                        item[name] = value is not None and all(v is not None for v in value)
                    receipt = receipts[f'matchDetails-{rid}.json']
                    profile = shot_profile(raw, prior, clock(receipt['query_time_utc']))
                    opponent, = [k for k in profile if k != team]
                    oriented.append((profile[team], profile[opponent]))
                    item['state'] = 'complete'
                except ValueError as exc:
                    item['state'] = str(exc)
            slots.append(item)
        histories.append(oriented)
    both = all(len(v) == 3 for v in histories)
    result = {'id': f['match_id'], 'cutoff': f['cutoff_at'], 'kickoff': f['kickoff_at'], 'slots': slots}
    if both:
        result['candidate'] = matrix(*histories).tolist()
        result['same_xg_poisson'] = matrix(*histories, at_mean=True).tolist()
    return result


def run(directory):
    index, receipts = read_index(directory)
    completed = clock(index['queried_at_utc'])
    rows = [attempt_fixture(directory, event, receipts, completed)
            for event in sorted(index['target_matches'], key=lambda r: r['espn_event_id'])]
    slots = [s for row in rows for s in row['slots']]
    teams = sum(sum(s['state'] == 'complete' for s in row['slots'][i:i+3]) == 3
                for row in rows for i in (0,3))
    return rows, dict(fixtures=len(rows), team_targets=2*len(rows), complete_team_targets=teams,
                      both_complete=sum('candidate' in r for r in rows), slot_target=len(slots),
                      unique_prior_fixtures=len({s['prior'] for s in slots if 'prior' in s}),
                      slot_status=dict(Counter(s['state'] for s in slots)),
                      native_both_sides={k: sum(bool(s.get(k)) for s in slots)
                                         for k in ('shots_inside_box','big_chance','expected_goals_non_penalty')})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report-directory', required=True, type=Path)
    p.add_argument('--blind-output', required=True, type=Path)
    args = p.parse_args()
    records, summary = run(args.report_directory)
    with args.blind_output.open('x') as out:
        json.dump(records, out, separators=(',',':'), allow_nan=False)
    print(json.dumps(summary, indent=2, sort_keys=True))
