"""Read-only public history ingestion. Observation time is never backdated."""
import concurrent.futures
import csv
import hashlib
import io
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / 'data' / 'v2'

def download(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'WCPP-research/0.2'})
    with urllib.request.urlopen(req, timeout=40) as response:
        raw = response.read()
    return raw, datetime.now(timezone.utc).isoformat()

def collect_one(job):
    name, url, family = job
    try:
        raw, observed = download(url)
        (ROOT / (name + ('.csv' if family == 'epl' else '.json'))).write_bytes(raw)
        rows, excluded = [], 0
        if family == 'epl':
            for x in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
                if not x.get('FTHG') or not x.get('FTAG'):
                    excluded += 1
                    continue
                date = datetime.strptime(x['Date'], '%d/%m/%Y' if len(x['Date']) == 10 else '%d/%m/%y').date().isoformat()
                odds = {}
                for key in ['B365H', 'B365D', 'B365A', 'B365>2.5', 'B365<2.5']:
                    if x.get(key):
                        try: odds[key] = float(x[key])
                        except ValueError: pass
                rows.append(dict(match_id=name+':'+x['Date']+':'+x['HomeTeam']+':'+x['AwayTeam'],
                    cohort='EPL', home=x['HomeTeam'], away=x['AwayTeam'], date=date,
                    home_goals=int(x['FTHG']), away_goals=int(x['FTAG']), score_basis='regulation_90',
                    source_url=url, observed_at=observed, historical_odds=odds,
                    odds_timestamp_verified=False, first_known_at=None, neutral=False))
        else:
            payload=json.loads(raw)
            for e in payload.get('events', []):
                c=e['competitions'][0]
                # Explicitly reject after-extra-time and shootout final scores.
                if c.get('status', {}).get('type', {}).get('name') != 'STATUS_FULL_TIME':
                    excluded += 1
                    continue
                h=next(t for t in c['competitors'] if t['homeAway']=='home')
                a=next(t for t in c['competitors'] if t['homeAway']=='away')
                rows.append(dict(match_id=family+':'+e['id'], cohort=family,
                    home=h['team']['displayName'], away=a['team']['displayName'],
                    date=e['date'][:10], kickoff_at=e['date'], home_goals=int(h['score']), away_goals=int(a['score']),
                    neutral=bool(c.get('neutralSite',False)), score_basis='regulation_90',
                    source_url=url, observed_at=observed, first_known_at=None))
        return rows, dict(name=name,url=url,rows=len(rows),excluded=excluded,
            bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),observed_at=observed,
            coverage='380-match season check' if family=='epl' else 'annual API response; completeness not independently certified')
    except Exception as exc:
        return [], dict(name=name,url=url,error=str(exc))

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    jobs=[('epl-'+s,'https://www.football-data.co.uk/mmz4281/'+s+'/E0.csv','epl')
          for s in ['2021','2122','2223','2324','2425','2526']]
    jobs += [(league+'-'+str(year),f'https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard?dates={year}&limit=1000',league)
             for league in ['uefa.nations','fifa.friendly','usa.1'] for year in range(2022,2027)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(collect_one,jobs))
    datasets={};seen=set();audit=[]
    for rows,meta in results:
        audit.append(meta)
        for row in rows:
            key=(row['cohort'],row['match_id'])
            if key in seen: raise ValueError('duplicate match ID')
            seen.add(key);datasets.setdefault(row['cohort'],[]).append(row)
    for family,rows in datasets.items():
        rows.sort(key=lambda x:(x['date'],x['match_id']))
        (ROOT/(family+'-history.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    (ROOT/'collection-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(json.dumps({'datasets':{k:len(v) for k,v in datasets.items()},'failures':[x for x in audit if 'error' in x]},ensure_ascii=False))

if __name__=='__main__': main()
