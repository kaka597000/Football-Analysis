"""Recover the November 5–6 original market evidence from pinned Data objects, input fields only."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
from urllib.parse import urlsplit

from research.analysis_freeze import aware, verify
from study import RULES, packed, write


PACKS = {'20261105-europa-conference-analysis': '2470d20a20ca1b346792b6d9b1725e32255e047ca899a8baf81143f81796e299',
         '20261106-aleague-analysis': '7ccf7f258131eedcc1ea187ffee93c666df4a21db1c2e6e77eccda918052b9ba'}


def price(leg):
    try:
        american = int(leg['odds'])
        if abs(american) >= 100:
            return 1 + (american / 100 if american > 0 else -100 / american)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def endpoint(quote, tag):
    totals = quote.get('total') or {}
    over = (totals.get('over') or {}).get(tag) or {}
    under = (totals.get('under') or {}).get(tag) or {}
    try:
        lines = [s['line'] for s in (over, under)]
        if not lines[0].startswith('o') or not lines[1].startswith('u') or float(lines[0][1:]) != float(lines[1][1:]):
            return None
        return dict(line=float(lines[0][1:]), over=price(over), under=price(under))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def project_quote(odds):
    books = [q for q in odds or [] if q and (q.get('provider') or {}).get('name') == 'DraftKings']
    if len(books) != 1:
        return dict(latest_h2h=None, opening_total=None, current_total=None)
    q = books[0]
    money = q.get('moneyline') or {}
    h2h = {leg: price((money.get(leg) or {}).get('close') or {}) for leg in ('home', 'draw', 'away')}
    return dict(latest_h2h=h2h, opening_total=endpoint(q, 'open'), current_total=endpoint(q, 'close'))


def source_member(name):
    return 'WCPP/' + name.split('WCPP/', 1)[1]


def match_snapshot(item, source_url, fixture):
    eid = fixture['match_id'].removeprefix('espn:')
    if 'events' in item:
        events = [x for x in item['events'] if x['id'] == eid]
        if len(events) != 1:
            return None
        event = events[0]
        league = fixture['competition']
        if not urlsplit(source_url).path.endswith(f'/soccer/{league}/scoreboard'):
            return None
        if league not in {l.get('slug') for l in item.get('leagues', [])}:
            return None
        comps, stage = event['competitions'], event['season'].get('slug')
        stage_ok = league in ('uefa.europa', 'uefa.europa.conf') and stage == fixture['stage'] == 'league-phase'
    else:
        header = item['header']
        if header['id'] != eid or header['league']['slug'] != fixture['competition']:
            return None
        comps = header['competitions']
        stage_ok = (fixture['competition'] == 'aus.1' and fixture['stage'] == 'regular' and
                    header['season'].get('name', '').casefold().endswith(', regular season'))
    if len(comps) != 1 or not stage_ok:
        return None
    comp = comps[0]
    sides = {team['homeAway']: 'espn:'+str(team['team']['id']) for team in comp['competitors']}
    if (str(comp['id']) != eid or comp['status']['type']['state'] != 'pre'
            or aware(comp['date']) != aware(fixture['kickoff_at'])
            or sides != {k: fixture[k] for k in ('home', 'away')}):
        return None
    return project_quote(comp.get('odds') if 'events' in item else item.get('odds'))


def load_batch(repo, batch, expected):
    def get(name):
        path = f'workspace-updates/{batch}/{name}'
        return subprocess.check_output(['git', '-C', str(repo), 'show', RULES['data_snapshot']+':'+path])
    manifest = json.loads(get('manifest.json'))
    data = b''.join(get(part['path']) for part in manifest['ordered_parts'])
    if hashlib.sha256(data).hexdigest() != expected or expected != manifest['archive_sha256']:
        raise ValueError('batch identity mismatch')
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = tar.getnames()
        def read(name):
            return tar.extractfile(name).read()
        def load(suffix):
            path, = [p for p in names if p.endswith(suffix)]
            return json.loads(read(path))
        if batch.startswith('20261105'):
            refs = [r['receipt'] for r in load('/analysis-records.json')]
            entries = [load('/espn-' + league + '-metadata.json') for league in ('uefa.europa', 'uefa.europa.conf')]
        else:
            refs = [load('/analysis-receipts.json')['comprehensive']]
            entries = [load('/espn_event_summary.meta.json')]
        capture = {(e['sha256'], e['url']): e.get('queried_at') or e['queried_at_utc']
                   for e in entries if e['http_status'] == 200}
        for ref in refs:
            record = verify(json.loads(read(source_member(ref['path']))))
            f = record['fixture']
            if (record['record_sha256'] != ref['sha256'] or record['freeze_id'] != ref['freeze_id'] or
                    record['evaluation_slot'] != 'FIRST_PREMATCH' or record['track'] != 'comprehensive'
                    or record['stage'] != 'prematch' or not aware(f['cutoff_at']) <= aware(record['recorded_at']) < aware(f['kickoff_at'])):
                raise ValueError('first freeze mismatch')
            row = dict(id=f['match_id'], batch=batch, date=f['kickoff_at'][:10], provider='DraftKings',
                       home_id=f['home'], away_id=f['away'], competition=f['competition'], stage=f['stage'],
                       source_ids=f.get('source_ids'), cutoff_at=f['cutoff_at'], kickoff_at=f['kickoff_at'],
                       freeze_sha256=ref['sha256'], recorded_at=record['recorded_at'])
            snaps = []
            for s in f['sources']:
                url, hh = s['source_url'], s['response_sha256']
                path = urlsplit(url).path
                captured = capture.get((hh, url))
                if (not path.endswith(('/scoreboard', '/summary')) or urlsplit(url).hostname != 'site.api.espn.com'
                        or not captured or aware(captured) != aware(s['query_time'])
                        or aware(captured) >= aware(f['cutoff_at'])):
                    continue
                raw = read(source_member(s.get('source_file') or s['raw_file']))
                if hashlib.sha256(raw).hexdigest() != hh:
                    raise ValueError('original response hash mismatch')
                q = match_snapshot(json.loads(raw), url, f)
                if q is not None:
                    snaps.append((aware(captured), hh, q))
            # These legacy firsts omit score_basis. The bound native league phase/regular
            # season (no extra-time tie breaker) and full-match total identify the period.
            ordinary_match = (f.get('score_basis', 'regulation_90') == 'regulation_90' and
                              f.get('total_quote', {}).get('market') == 'standard_total' and
                              f.get('gender') in ('men', 'male') and f.get('age') == 'senior')
            if snaps and f.get('identity_verified') and ordinary_match:
                stamp, hh, q = max(snaps, key=lambda s: s[0])
                if any(s[0] == stamp and s[2] != q for s in snaps):
                    raise ValueError('different book panels at the same cutoff')
                row.update(q, capture_at=stamp.isoformat(), raw_sha256=hh,
                           regulation_basis='native_league_stage_and_full_match_market')
            else:
                row['excluded'] = 'missing_original_identity_or_bound_board'
            yield row


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    rows = [r for name, key in PACKS.items() for r in load_batch(args.data, name, key)]
    write(args.out, b''.join(packed(r) for r in rows))
    from collections import Counter
    from research_program.same_line_motion import observation
    print(len(rows), dict(Counter(observation(r)[0] for r in rows)))
