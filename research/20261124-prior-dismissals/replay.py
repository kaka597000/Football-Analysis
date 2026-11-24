"""Prepare label-free projections, then grade their agreed digest in a separate command."""

import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

from research.analysis_freeze import aware
from research_program.prior_dismissals import fit_season, forecast
from research_program.replay import _finished_espn_label

from archive import (Archives, CODES, EARLY, PIN, PROTOCOL, blob, checked, goals, historical_line,
                     identity, resolve_club, sha)


def packed(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def put(path, data):
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f'output already exists with different content: {path.name}')
    else:
        path.write_bytes(data)


def verified_summary(repo, pin, r, directory):
    ref = r['raw_reference']
    url = urlparse(ref['source_url'])
    if (r['source_url'] != ref['source_url'] or url.scheme != 'https'
            or url.hostname not in ('site.api.espn.com','site.web.api.espn.com')
            or url.path.split('/')[-2:] != [r['cohort'], 'summary']
            or parse_qs(url.query).get('event') != [r['match_id'].removeprefix('espn:')]
            or r['score_basis'] != 'regulation_90' or r['stage'] != 'regular'
            or aware(ref['captured_at']) < aware(r['kickoff_at']) + timedelta(minutes=90)
            or ref['path'] != f"raw/{ref['sha256']}.json.gz"):
        raise ValueError('unexpected official result identity, scope or capture')
    body = checked(gzip.decompress(blob(repo, pin, ref['path'])), ref['sha256'])
    path = directory/ref['sha256']
    path.write_bytes(body)
    _finished_espn_label(r, {'url':ref['source_url']}, path)
    return json.loads(body)['header']['competitions'][0]


def prior_aliases(archives, before, directory):
    repo = archives.repo
    anchored = defaultdict(lambda: defaultdict(set))
    result = defaultdict(set)
    for r in map(json.loads, blob(repo, EARLY, 'normalized-results.jsonl').splitlines()):
        if (r['cohort'] not in CODES or r['stage'] != 'regular' or r['score_basis'] != 'regulation_90'
                or aware(r['raw_reference']['captured_at']) >= before):
            continue
        try:
            event = verified_summary(repo, EARLY, r, directory)
        except ValueError:
            continue
        teams = {}
        for side in event['competitors']:
            team = side['team']
            names = {team[k] for k in ('displayName','shortDisplayName','name','location') if team.get(k)}
            teams[side['homeAway']] = names
            result[r['cohort'], 'espn:'+str(team['id'])].update(names)
        season, source_clock = archives.season(r['cohort'],2026)
        if source_clock is None or source_clock >= before:
            continue
        # Two independent past fixtures may corroborate a name variant on the same side.
        # Each must have a uniquely matching league/day, 90-minute score, and opposite club name.
        prior_day = aware(r['kickoff_at']).date()
        if not date(2026,7,1) <= prior_day <= date(2026,11,8):
            continue
        possibles = [f for f in season if f['day']==prior_day
                     and goals(f['raw'])==(r['home_goals'],r['away_goals'])]
        for side,other in (('home','away'),('away','home')):
            matches = [f for f in possibles if resolve_club(teams[other],[f[other]])]
            if len(matches)==1:
                anchored[r['cohort'],r[side]][matches[0][side]].add(r['match_id'])
    for key,names in anchored.items():
        if len(names)==1:
            name, seen = next(iter(names.items()))
            if len(seen)>=2:
                result[key].add(name)
    return result


def fixture_key(league, r):
    return '|'.join((league, r['day'].isoformat(), r['home'], r['away']))


def fit_input(rows, year):
    # Read target scores and target cards only via the separate grading command.
    return fit_season((dict(day=r['day'], home=r['home'], away=r['away'], goals=goals(r['raw']),
                            HR=r['raw'].get('HR'), AR=r['raw'].get('AR'))
                       for r in rows if date(year, 7, 1) <= r['day'] <= date(year, 11, 8)), year)


def historical(archives):
    records, training = [], []
    for window, years in list(PROTOCOL['season_start_years'].items())[:2]:
        for year in years:
            for league in CODES:
                rows, _ = archives.season(league, year)
                fit = fit_input(rows, year)
                training.append(dict(window=window, year=year, league=league, **fit[1]))
                for r in rows:
                    if not date(year, 11, 20) <= r['day'] <= date(year, 12, 31):
                        continue
                    line = historical_line(r['raw'])
                    prediction = forecast(fit, r['day'], r['home'], r['away'], line)
                    records.append(dict(window=window, league=league, id=fixture_key(league,r),
                                        day=r['day'].isoformat(), line=line, **prediction))
    return records, training


def clocked(archives, directory):
    fixtures = archives.targets()
    aliases = prior_aliases(archives, min(aware(f['cutoff_at']) for f in fixtures), directory)
    fits = {}
    training = []
    records = []
    for r in fixtures:
        league = r['cohort']
        day = aware(r['kickoff_at']).date()
        entry = dict(window='clocked_reconstruction', league=league, id=r['match_id'],
                     day=day.isoformat(), line=None, kickoff=r['kickoff_at'], cutoff=r['cutoff_at'],
                     sides=[r['home'], r['away']])
        records.append(entry)
        if r['match_id'] in archives.r7_ids or identity(r) in archives.r7_keys:
            entry['status'] = 'excluded_R7'
            continue
        if league not in CODES:
            entry['status'] = 'no_season_source'
            continue
        rows, capture = archives.season(league, 2026)
        if capture is None or capture >= aware(r['cutoff_at']):
            entry['status'] = 'no_season_source_before_cutoff'
            continue
        f = archives.original(r)
        if f is None:
            entry['status'] = 'missing_bound_first_comprehensive'
            continue
        if league not in fits:
            fits[league] = fit_input(rows, 2026)
            training.append(dict(window='clocked_reconstruction', year=2026, league=league, **fits[league][1]))
        fit = fits[league]
        clubs = [resolve_club({f[side+'_name'], *aliases[league,f[side]]}, fit[0][0].teams)
                 for side in ('home','away')]
        line = archives.quote_line(f)
        entry.update(line=line, freeze_sha256=r['freeze_reference']['sha256'], **forecast(fit, day, *clubs, line))
    return records, training


def prepare(repo, out):
    archives = Archives(repo)
    records, training = historical(archives)
    with TemporaryDirectory() as tmp:
        recent, new_training = clocked(archives, Path(tmp))
    records.extend(recent)
    records.sort(key=lambda r:(r['window'],r['day'],r['league'],r['id']))
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('duplicate forecast identity')
    data = b''.join(packed(r) for r in records)
    put(out/'forecasts.jsonl', data)
    put(out/'training.json', packed(training+new_training))
    lock = {'forecasts_sha256':sha(data), 'training_sha256':sha(packed(training+new_training)),
            'rows':len(records), 'paired':sum(r['status']=='paired' for r in records),
            'clocked_population':len(recent), 'r7_checked':len(archives.r7_ids),
            'r7_excluded':sum(r['status']=='excluded_R7' for r in recent)}
    put(out/'forecast-lock.json', json.dumps(lock,indent=2).encode()+b'\n')
    print(json.dumps(lock))


def score(repo, run, lockpath, out):
    from metrics import report
    lock = json.loads(lockpath.read_bytes())
    forecast_data = checked((run/'forecasts.jsonl').read_bytes(), lock['forecasts_sha256'])
    checked((run/'training.json').read_bytes(), lock['training_sha256'])
    records = list(map(json.loads, forecast_data.splitlines()))
    archives = Archives(repo)
    norm = defaultdict(list)
    for r in map(json.loads, blob(repo,PIN,'normalized-results.jsonl').splitlines()):
        norm[r['match_id']].append(r)
    historical_scores = {}
    for window, years in list(PROTOCOL['season_start_years'].items())[:2]:
        for year in years:
            for league in CODES:
                for r in archives.season(league,year)[0]:
                    if date(year,11,20) <= r['day'] <= date(year,12,31):
                        historical_scores[fixture_key(league,r)] = goals(r['raw'])
    labels, reasons = {}, Counter()
    with TemporaryDirectory() as tmp:
        for r in records:
            if r['status'] != 'paired':
                continue
            if r['window'] != 'clocked_reconstruction':
                if historical_scores[r['id']] is not None:
                    labels[r['id']] = historical_scores[r['id']]
                else:
                    reasons['missing_historical_score'] += 1
                continue
            candidates = [x for x in norm[r['id']] if x['cohort'] == r['league']
                          and aware(x['kickoff_at']) == aware(r['kickoff']) and [x['home'],x['away']]==r['sides']]
            if len(candidates) != 1:
                reasons['missing_or_ambiguous_result'] += 1
                continue
            result = candidates[0]
            try:
                verified_summary(repo,PIN,result,Path(tmp))
            except ValueError as exc:
                reasons[str(exc)] += 1
                continue
            labels[r['id']] = (result['home_goals'], result['away_goals'])
    value = report(records, labels, json.loads((run/'training.json').read_bytes()))
    value.update(lock=lock, label_exclusions=dict(reasons))
    put(out/'results.json', (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode())
    print(json.dumps({w:s['paired_scores'] for w,s in value['windows'].items()}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    for name in ('prepare','grade'):
        sub = commands.add_parser(name)
        sub.add_argument('--data-repo',required=True,type=Path)
        sub.add_argument('--out',required=True,type=Path)
        if name == 'grade':
            sub.add_argument('--from-run',required=True,type=Path)
            sub.add_argument('--lock',required=True,type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    if args.command == 'prepare':
        prepare(args.data_repo,args.out)
    else:
        score(args.data_repo,args.from_run,args.lock,args.out)
