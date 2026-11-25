"""Collect and replay a date-stratified sample from the public Phoenix archive."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import Event, Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from phoenix import ARCHIVE, wall_seconds
from . import continuation_study as r7
from . import r8_market_movement as movement
from .goal_state_study import write_new
from .r8_score_study import read

SOURCE = 'https://data.soccerdreamai.com/api/odds-archive'
SAMPLE_SIZE = 120
PAGE_SIZE = 100
MINIMUM_REQUEST_INTERVAL = .28
STOP = Event()
REQUEST_CLOCK = Lock()
next_request = 0.


def fetch(route, cache):
    global next_request
    if not cache.is_file():
        with REQUEST_CLOCK:
            time.sleep(max(0.,next_request - time.monotonic()))
            if STOP.is_set():
                raise RuntimeError('public provider requested backoff')
            next_request = time.monotonic() + MINIMUM_REQUEST_INTERVAL
        try:
            with urlopen(Request(SOURCE + route, headers={'Accept':'application/json'}), timeout=12) as response:
                raw = response.read()
        except HTTPError as error:
            if error.code in (401, 403, 429):
                STOP.set()
                raise RuntimeError('public provider access stopped: HTTP ' + str(error.code)) from error
            raise
        json.loads(raw)
        cache.write_bytes(raw)
    return read(cache)


def sample(fixtures):
    cut1, cut2, stop = movement.BOUNDS
    windows = (
        [g for g in fixtures if g['kick'][:10] < cut1],
        [g for g in fixtures if cut1 <= g['kick'][:10] < cut2],
        [g for g in fixtures if cut2 <= g['kick'][:10] < stop],
    )
    def ranks(games):
        n = min(SAMPLE_SIZE,len(games))
        return [games[(2*i+1)*len(games)//(2*n)] for i in range(n)]
    return [g for games in windows for g in ranks(games)]


def catalogue(name, code, raw):
    def page(n):
        route = '/matches?' + urlencode(dict(page=n,size=PAGE_SIZE,league=name,q=''))
        return fetch(route, raw/f'{code}-page-{n}.json')
    first = page(1)
    pages = (first['total'] + PAGE_SIZE - 1)//PAGE_SIZE
    games = first['rows'] + [g for n in range(2,pages+1) for g in page(n)['rows']]
    if len(games) != first['total'] or len({g['id'] for g in games}) != len(games):
        raise ValueError('Phoenix catalogue count or identity differs')
    return sorted(games,key=lambda g:(g['kick'],g['id']))


def normalize(fixture, detail, code, evidence_hash):
    key = ('id','league','kick','home','away','score')
    if any(fixture[k] != detail[k] for k in key):
        raise ValueError('Phoenix detail and catalogue do not match')
    if code != ARCHIVE[detail['league']]:
        raise ValueError('Phoenix competition mismatch')
    if sum(len(book['track']) for book in detail['bookmakers']) != fixture['odds_rows']:
        raise ValueError('Phoenix price count does not match published catalogue')
    points = []
    for book in detail['bookmakers']:
        for point in book['track']:
            lag = wall_seconds(detail['kick'],point.get('t'))
            identity = dict(id='phoenix:'+str(detail['id']), competition=code,
                date=detail['kick'][:10], home=detail['home'],away=detail['away'])
            points.append(dict(**identity, source='phoenix_public', bookmaker=book['code'],
                market='1x2', seconds_to_kickoff=lag, odds=dict(home=point.get('h'),draw=point.get('d'),
                away=point.get('a')), display_quote_time=point.get('t'), evidence_sha256=evidence_hash))
    return points


def jsonlines(path, rows):
    with path.open('x', encoding='utf8') as stream:
        for row in rows:
            stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')


def measure(name, code, raw, out):
    selected = sample(catalogue(name,code,raw))
    rows, quotes, issues, receipts = [], [], [], []
    def details(g):
        if not g['odds_rows']:
            return [], None, 'NO_PUBLISHED_TRACK'
        route = '/match/' + str(g['id'])
        path = raw/f'detail-{g["id"]}.json'
        try:
            data = fetch(route,path)
            digest = sha256(path.read_bytes()).hexdigest()
            points = normalize(g,data,code,digest)
            return points, dict(file=path.name,sha256=digest,url=SOURCE+route), None
        except (HTTPError,URLError,TimeoutError,ValueError,RuntimeError,KeyError) as error:
            return [], None, type(error).__name__ + ':' + str(error)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for g,(points,receipt,error) in zip(selected,pool.map(details,selected),strict=True):
            goals = re.fullmatch(r'(\d+)\s*[-:]\s*(\d+)', g['score'] or '')
            if not goals or g['home'] == g['away'] or ARCHIVE.get(g['league']) != code:
                raise ValueError('sampled row lacks a regular domestic final score and identity')
            contemporary, _, _ = movement.endpoints(points)
            source_hash = receipt['sha256'] if receipt else sha256(json.dumps(g,ensure_ascii=False,
                sort_keys=True,separators=(',',':')).encode()).hexdigest()
            row = dict(id='phoenix:'+str(g['id']),competition=code, stage='regular_league',
                date=g['kick'][:10], home=g['home'], away=g['away'], neutral=None,
                home_goals=int(goals[1]),away_goals=int(goals[2]),paired_market=False,
                h2h=dict(zip(('home','draw','away'),(1/contemporary).tolist())) if contemporary is not None else None,
                total=None,source_sha256=source_hash)
            rows.append(row)
            quotes.extend(points)
            if error:
                issues.append(dict(id=row['id'],status=error))
            if receipt:
                receipts.append(dict(id=row['id'],**receipt))
    folder = out/code
    folder.mkdir(parents=True)
    panel = folder/'panel.jsonl'
    points = folder/'points.jsonl'
    jsonlines(panel,rows)
    jsonlines(points,quotes)
    write_new(folder/'source-receipts.json',dict(captured_at=datetime.now().astimezone().isoformat(),
        source=SOURCE,detail_receipts=receipts,missing=issues,
        listing_hashes={p.name:sha256(p.read_bytes()).hexdigest() for p in sorted(raw.glob(f'{code}-page-*.json'))}))
    r7.run(panel,code,folder/'r7-t1',bounds=movement.BOUNDS,mode='t1')
    report = movement.run(panel,points,read(folder/'r7-t1/fit.json'),code,folder/'movement')
    print(code,report['status'],json.dumps(report['coverage']),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args = parser.parse_args()
    args.cache.mkdir(mode=0o700,parents=True,exist_ok=True)
    for name,code in ARCHIVE.items():
        measure(name,code,args.cache,args.out)
