"""Project only past completed match data; keep labels separate from target market inputs."""

import csv
from datetime import datetime
import hashlib
import io
from itertools import groupby
import json
from operator import itemgetter
from pathlib import Path
import tarfile

from research_program.halftime_balance import TeamHistory

HERE = Path(__file__).resolve().parent
PROTOCOL = json.loads((HERE/'protocol.json').read_bytes())
LEAGUES = PROTOCOL['domestic_sources']
FIT_SEASONS = ('1920','2021','2122')
LATER_SEASONS = ('2223','2324','2425')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def packed(value):
    return (json.dumps(value, separators=(',', ':'), sort_keys=True, ensure_ascii=True, allow_nan=False)+'\n').encode()


def store(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_file():
        if path.read_bytes() != data:
            raise ValueError(f'different previous output at {path.name}')
    else:
        with path.open('xb') as f:
            f.write(data)


def scores(raw, prefix):
    cells = [str(raw.get(prefix+k,'')).strip() for k in ('HG','AG')]
    return tuple(map(int,cells)) if all(c.isascii() and c.isdecimal() for c in cells) else None


def book(raw):
    try:
        h,d,a,o,u = (float(raw[k]) for k in ('B365H','B365D','B365A','B365>2.5','B365<2.5'))
    except (KeyError,ValueError,TypeError):
        return None
    if not all(1 < p < float('inf') for p in (h,d,a,o,u)):
        return None
    return {'h2h':{'home':h,'draw':d,'away':a},'total':{'line':2.5,'over':o,'under':u}}


def seasons(archive, selected):
    if sha(archive) != PROTOCOL['market_archive_sha256']:
        raise ValueError('wrong market source edition')
    with tarfile.open(archive) as tar:
        for season in selected:
            for code,league in LEAGUES.items():
                name = 'raw/close/'+season+'_'+code+'.csv'
                data = tar.extractfile(name).read().decode('utf-8-sig')
                rows = []
                seen = set()
                for raw in csv.DictReader(io.StringIO(data)):
                    if not raw.get('Date'):
                        continue
                    day = datetime.strptime(raw['Date'],'%d/%m/%Y').date()
                    home, away = raw['HomeTeam'].strip(), raw['AwayTeam'].strip()
                    key = '|'.join((league,day.isoformat(),home,away))
                    if raw['Div'] != code or key in seen or not home or not away or home==away:
                        raise ValueError('ambiguous historical match identity')
                    seen.add(key)
                    rows.append(dict(day=day,home=home,away=away,id=key,raw=raw))
                yield season,league,sorted(rows,key=itemgetter('day','home','away'))


def projected_games(archive, training):
    for season,league,rows in seasons(archive, FIT_SEASONS if training else LATER_SEASONS):
        state = TeamHistory()
        for day, group in groupby(rows,itemgetter('day')):
            fixtures = list(group)
            for r in fixtures:
                if not training and (day.year not in (2023,2024,2025) or not 2 <= day.month <= 4):
                    continue
                s, status = state.signal(day,r['home'],r['away'])
                row = dict(id=r['id'],day=day.isoformat(),league=league,window='historical',
                           signal=s,feature_status=status,market=book(r['raw']))
                if training:
                    row['label'] = scores(r['raw'],'FT')
                yield row
            # All predictions on this date precede the home/away updates. Later dates alone can use these stats.
            for r in fixtures:
                state.observe(day,r['home'],r['away'],scores(r['raw'],'HT'),scores(r['raw'],'FT'))


def historical_labels(archive):
    return {r['id']:scores(r['raw'],'FT') for _,_,rows in seasons(archive,LATER_SEASONS)
            for r in rows if r['day'].year in (2023,2024,2025) and 2 <= r['day'].month <= 4}
