"""Read only pinned Git objects. Keep original supplier payloads out of study outputs."""

import csv
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
import unicodedata
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from research.analysis_freeze import aware, verify
from research_program.future_validation import quote_audit

HERE = Path(__file__).resolve().parent
PROTOCOL = json.loads((HERE / 'protocol.json').read_bytes())
PIN = PROTOCOL['history_snapshot']
EARLY = PROTOCOL['captured_training_snapshot']
GAP = 'workspace-updates/20261123-score-gap-repair'
KPI = 'workspace-updates/20261123-kpi-optimization'
TRANSFER = 'workspace-updates/20261118-transformer/transfer/raw'
R7 = 'workspace-updates/20261123-prematch-literature/first-issues.tar.gz'
CODES = dict(zip(PROTOCOL['competitions'], ('E0', 'SP1', 'D1', 'I1', 'F1', 'N1')))


def blob(repo, pin, path):
    return subprocess.check_output(['git', '-C', str(repo), 'show', f'{pin}:{path}'])


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked(data, expected):
    if sha(data) != expected:
        raise ValueError('archive or source identity mismatch')
    return data


class Archives:
    def __init__(self, repo):
        self.repo = repo
        pins = PROTOCOL['input_sources']
        def archive(data, key):
            return tarfile.open(fileobj=io.BytesIO(checked(data, pins[key])))
        parts = [blob(repo, PIN, f'{GAP}/raw-sources.tar.gz.part-{i:02}') for i in range(11)]
        self.early = archive(b''.join(parts), 'earlier_archive_sha256')
        self.assets = archive(blob(repo, PIN, f'{KPI}/source-assets.tar.gz'), 'later_and_freezes_sha256')
        with archive(blob(repo, PIN, R7), 'r7_exclusion_sha256') as t:
            records = (json.load(t.extractfile(n)) for n in t.getnames() if n.endswith('.json'))
            issues = [verify(r) for r in records if r.get('track') == 'research-prematch-continuation-20261123']
        self.r7_ids = {r['match_id'] for r in issues}
        self.r7_keys = {identity(r['fixture']) for r in issues}
        if len(self.r7_ids) != 192:
            raise ValueError('R7 population mismatch')
        self.raw_index = {(r['competition'], r['season_code']): r
                          for r in json.loads((HERE / 'sources.json').read_bytes())}
        metadata = checked(blob(repo, EARLY, f'{TRANSFER}/source-metadata.json'),
                           'd3d952758765f4a8b9b0f338840742b3a6a24ee4ed68f149b863a4de313f3ff9')
        self.current = {r['file']: r for r in json.loads(metadata)}
        self.committed = aware(subprocess.check_output(
            ['git', '-C', str(repo), 'show', '-s', '--format=%cI', EARLY], text=True).strip())

    @lru_cache(maxsize=None)
    def season(self, league, year):
        season = f'{year % 100:02}{(year+1) % 100:02}'
        file = f'{season}_{CODES[league]}.csv'
        clocks = None
        if year < 2018:
            src = self.raw_index[league, season]
            data = checked(self.early.extractfile(src['member']).read(), src['sha256'])
        elif year == 2026:
            if file not in self.current:
                return [], None
            src = self.current[file]
            if src['url'] != f'https://www.football-data.co.uk/mmz4281/{season}/{CODES[league]}.csv':
                raise ValueError('incorrect competition or year in source')
            data = checked(blob(self.repo, EARLY, f'{TRANSFER}/{file}'), src['sha256'])
            clocks = max(aware(src['captured_at']), self.committed)
        else:
            data = self.assets.extractfile(f'raw/close/{file}').read()
        rows = []
        ids = set()
        for raw in csv.DictReader(io.StringIO(data.decode('utf-8-sig', errors='surrogateescape'))):
            if not raw.get('Date') or not raw.get('HomeTeam') or not raw.get('AwayTeam'):
                continue
            if raw['Div'] != CODES[league]:
                raise ValueError('league mismatch')
            stamp = raw['Date'].strip()
            day = datetime.strptime(stamp, '%d/%m/%Y' if len(stamp.split('/')[-1]) == 4 else '%d/%m/%y').date()
            home, away = raw['HomeTeam'].strip(), raw['AwayTeam'].strip()
            key = (day, home, away)
            if home == away or key in ids:
                raise ValueError('ambiguous home/away fixture')
            ids.add(key)
            # The later target rows enter the predictor through dispatch fields only.
            rows.append({'day': day, 'home': home, 'away': away, 'raw': raw})
        return rows, clocks

    def targets(self):
        fixtures = {}
        for name in self.assets.getnames():
            if not name.endswith('timestamp-certified.jsonl.gz'):
                continue
            rows = gzip.decompress(self.assets.extractfile(name).read()).splitlines()
            for r in map(json.loads, rows):
                start, end = map(aware, ('2026-11-20T00:00:00+08:00','2026-11-22T00:00:00+08:00'))
                if not start <= aware(r['kickoff_at']) < end:
                    continue
                # Explicit safe projection: do not pass alternate fields or target labels on to forecasts.
                f = {k: r[k] for k in ('match_id','cohort','stage','home','away','kickoff_at',
                                        'cutoff_at','score_basis','freeze_reference')}
                if f['match_id'] in fixtures or identity(f) in {identity(x) for x in fixtures.values()}:
                    raise ValueError('duplicate reconstruction fixture')
                fixtures[f['match_id']] = f
        return list(fixtures.values())

    def original(self, r):
        name = 'issued/' + r['freeze_reference']['freeze_id'] + '.json'
        if name not in self.assets.getnames():
            return None
        record = verify(json.load(self.assets.extractfile(name)))
        f = record['fixture']
        if (record['track'] != 'comprehensive' or record['stage'] != 'prematch'
                or record['evaluation_slot'] != 'FIRST_PREMATCH' or record['revision'] != 1
                or record['record_sha256'] != r['freeze_reference']['sha256']
                or record['freeze_id'] != r['freeze_reference']['freeze_id']
                or record['match_id'] != f['match_id']
                or any(f[k] != r[k] for k in ('match_id','cohort','home','away','stage','score_basis'))
                or aware(f['kickoff_at']) != aware(r['kickoff_at'])
                or aware(f['cutoff_at']) != aware(r['cutoff_at'])
                or not aware(f['cutoff_at']) <= aware(record['recorded_at']) < aware(f['kickoff_at'])
                or f['stage'] != 'regular' or f['score_basis'] != 'regulation_90'
                or f.get('neutral') is not False or f.get('identity_verified') is not True):
            return None
        return f

    def quote_line(self, f):
        audit = quote_audit(f)
        if not audit['eligible']:
            return None
        q = audit['quote']
        snaps = [s for s in f.get('market_snapshots', []) if s['market'] == 'totals'
                 and all(s.get(k) == q.get(k) for k in ('match_id','bookmaker','line','source_sha256',
                                                        'source_url','quoted_at','query_time'))
                 and s['decimal_odds'] == {'over':q['over_odds'], 'under':q['under_odds']}]
        if len(snaps) != 1:
            return None
        path = f"raw/open-pregame/{q['source_sha256']}.json"
        if path not in self.assets.getnames():
            return None
        raw = json.loads(checked(self.assets.extractfile(path).read(), q['source_sha256']))
        events = [e for e in raw if e['id'] == snaps[0]['provider_match_id']
                  and aware(e['commence_time']) == aware(f['kickoff_at'])]
        if len(events) != 1:
            return None
        quotes = [m for b in events[0]['bookmakers'] if b['key'] == q['bookmaker']
                  for m in b['markets'] if m['key'] == 'totals'
                  and aware(m.get('last_update') or b['last_update']) == aware(q['quoted_at'])]
        for m in quotes:
            legs = [(o['name'].lower(), o['price']) for o in m['outcomes'] if o.get('point') == q['line']]
            if len(legs) == 2 and dict(legs) == snaps[0]['decimal_odds']:
                return q['line']
        return None


def identity(f):
    return f['cohort'], f['home'], f['away'], aware(f['kickoff_at'])


def goals(raw):
    fields = [str(raw.get(k, '')).strip() for k in ('FTHG', 'FTAG')]
    if all(x.isascii() and x.isdecimal() for x in fields):
        return tuple(map(int, fields))
    return None


def historical_line(raw):
    try:
        return 2.5 if all(1 < float(raw[k]) < float('inf') for k in ('B365>2.5', 'B365<2.5')) else None
    except (ValueError, TypeError, KeyError):
        return None


def club_key(name):
    name = ''.join(c for c in unicodedata.normalize('NFKD', name).casefold() if not unicodedata.combining(c))
    words = ''.join(c if c.isalnum() else ' ' for c in name).split()
    while words and words[0] in ('fc','ac','cf','afc'):
        words.pop(0)
    while words and words[-1] in ('fc','ac','cf','afc'):
        words.pop()
    return ' '.join(words)


def resolve_club(names, registry):
    matches = {n for n in registry if club_key(n) in {club_key(s) for s in names if s}}
    return next(iter(matches)) if len(matches) == 1 else None
