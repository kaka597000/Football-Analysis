"""Reconcile ESPN Elite two-period box scores with the original panel and daily scoreboards."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
from zoneinfo import ZoneInfo

BJT = ZoneInfo('Asia/Shanghai')


def read(path):
    return json.loads(path.read_bytes())


def unpack(raw, expected):
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('source hash mismatch: ' + expected)
    data = json.loads(raw)
    return data.get('response', data)


def sides(comp):
    return {team['homeAway']: team for team in comp['competitors']}


def utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def save_lines(path, rows):
    with path.open('x', encoding='utf-8') as f:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, sort_keys=True), file=f)


def prepare(r5, data_repo, intake, additional, out):
    out.mkdir(parents=True, exist_ok=False)
    with tarfile.open(r5/'models/afc.champions.tar.gz') as bundle:
        panel = {r['id'].split(':')[-1]: r for r in map(json.loads, bundle.extractfile('panel.jsonl'))}
    index = read(r5/'evidence/source-index.json')
    boards = {}
    for sha in {row['source_sha256'] for row in panel.values()}:
        ref = index[sha]
        if 'archive' in ref:
            with tarfile.open(r5/'evidence'/ref['archive']) as bundle:
                raw = gzip.decompress(bundle.extractfile(ref['member']).read())
        else:
            raw = subprocess.check_output(['git','-C', str(data_repo),'show',ref['commit']+':'+ref['repository_path']])
        boards[sha] = {e['id']: e for e in unpack(raw, sha)['events']}
    fixtures = {}
    names = defaultdict(Counter)
    for eid, row in panel.items():
        sha = row['source_sha256']
        event = boards[sha][eid]
        c = event['competitions'][0]
        for side, team in sides(c).items():
            names[team['id']][row[side]] += 1
        fixtures[eid] = (event, sha, row, row['sources'][0]['url'])
    for ref in read(intake/'scoreboard-index.json')['responses']:
        if ref['status'] != 'ok':
            raise ValueError('missing daily scoreboard: '+ref['url'])
        for e in unpack((intake/ref['relative_path']).read_bytes(), ref['sha256'])['events']:
            kickoff = utc(e['date'])
            if ('2026-10-13' <= kickoff.astimezone(BJT).date().isoformat() <= '2026-11-25'
                    and e['status']['type']['completed'] and e['id'] not in panel):
                fixtures[e['id']] = (e,ref['sha256'], None, ref['url'])
    summaries = {v['id'].split(':')[-1]: (intake/v['relative_path'], v['sha256'], v['url'])
                 for v in read(intake/'missing-reconciliation.json')['summary_responses'] if v.get('sha256')}
    for v in read(additional/'index.json'):
        eid = v['id'].split(':')[-1]
        summaries[eid] = (additional/(eid+'.json'), v['sha256'], v['url'])
    shots, extension, audit = [], [], []
    for eid, (event, board_sha, old, board_url) in fixtures.items():
        path, sha, url = summaries[eid]
        data = unpack(path.read_bytes(), sha)
        header = data['header']
        c, board = header['competitions'][0], event['competitions'][0]
        teams, booked = sides(c), sides(board)
        kickoff = utc(c['date'])
        day = kickoff.date().isoformat()
        match_id = 'afc.champions:'+eid
        if (str(c['id']) != eid or header['league']['slug'] != 'afc.champions' or utc(board['date']) != kickoff
                or any(str(teams[s]['id']) != str(booked[s]['id']) for s in ('home','away'))
                or (old and (day != old['date'] or match_id != old['id']))):
            raise ValueError('competition, date or side ID mismatch: '+eid)
        identity = dict(id=match_id, kickoff_utc=kickoff.isoformat(), date_bjt=kickoff.astimezone(BJT).date().isoformat(),
                        scoreboard_sha256=board_sha, summary_sha256=sha, team_ids=[teams[s]['id'] for s in ('home','away')])
        status = c['status']['type']
        periods = [teams[s].get('linescores',[]) for s in ('home','away')]
        if not (status['completed'] and status['shortDetail'] == 'FT' and all(len(v) == 2 for v in periods)):
            if not old:
                raise ValueError('additional result requires regulation-only confirmation: '+eid)
            audit.append(dict(identity, outcome='old_panel_score_only', reason=status['shortDetail']))
            continue
        goals = [sum(int(h['displayValue']) for h in v) for v in periods]
        if any(goals[i] != int(teams[s]['score']) or goals[i] != int(booked[s]['score'])
               or (old and goals[i] != old[s+'_goals']) for i,s in enumerate(('home','away'))):
            raise ValueError('two-halves result mismatch: '+eid)
        if old:
            row = old
        else:
            region = (board.get('group') or {}).get('name')
            if event['season']['slug'] != 'league-stage' or region not in ('East Region','West Region'):
                raise ValueError('unexpected new Elite stage: '+eid)
            row = dict(id=match_id, competition='afc.champions', date=day, season='2026/27',
                       stage='elite:league-phase:'+region.lower().replace(' ','-'), source_stage=region,
                       score_basis='verified_regulation_from_two_periods', source_sha256=sha, neutral=c.get('neutralSite'),
                       kickoff_utc=kickoff.isoformat(), date_bjt=identity['date_bjt'], leg=None, bookmaker=None,
                       paired_market=False, market_time_basis=None, h2h=None, total=None, line=None,
                       sources=[dict(url=board_url, sha256=board_sha), dict(url=url, sha256=sha)])
            for i,s in enumerate(('home','away')):
                team = teams[s]
                known = names[team['id']]
                row[s] = known.most_common(1)[0][0] if known else team['team']['displayName']
                row[s+'_goals'] = goals[i]
            extension.append(row)
        observed = {}
        box = {str(t['team']['id']): t['statistics'] for t in data['boxscore']['teams']}
        for s in ('home','away'):
            stat = [v for v in box.get(str(teams[s]['id']), []) if v.get('name') in ('shotsOnTarget','shotsOnGoal')]
            if len(stat) == 1 and str(stat[0]['displayValue']).isdigit():
                observed[s+'_sot'] = int(stat[0]['displayValue'])
        if len(observed) == 2:
            shots.append(dict(id=match_id, competition=row['competition'], date=row['date'], home=row['home'],
                              away=row['away'], **observed, source_sha256=sha))
        audit.append(dict(identity, outcome='paired_shots' if len(observed)==2 else 'missing_shots', phase=row['stage'],
                          goals=goals, sources=[board_url,url]))
    for filename, rows in [('extend',extension),('process',shots),('reconciliation',audit)]:
        save_lines(out/(filename+'.jsonl'), sorted(rows,key=lambda x:(x.get('date',''),x['id'])))
    counts = dict(original=len(panel), additional=len(extension), process=len(shots), outcomes=Counter(x['outcome'] for x in audit))
    (out/'coverage.json').write_text(json.dumps(counts,indent=2)+'\n')
    print(counts)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('r5','data_repo','intake','additional','out'):
        p.add_argument('--'+name.replace('_','-'), required=True, type=Path)
    prepare(**vars(p.parse_args()))
