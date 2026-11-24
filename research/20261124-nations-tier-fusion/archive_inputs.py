"""Read only signed predictions and original market evidence from pinned Git blobs."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tarfile
import unicodedata
from urllib.parse import urlsplit

from competition_scope import require
from nations_league.elo_reference import ALIASES
from research.analysis_freeze import aware, digest, verify
from research_program.future_validation import quote_audit
from research_program.market_baseline import _paired_capture, quote_only_matrices
from research_program.three_arm_model import _matrix, score_picks

HERE = Path(__file__).resolve().parent
SOURCES = json.loads((HERE / 'sources.json').read_bytes())
PREMATCH = ('p12', 'p13', 'p14', 'p14late', 'p15', 'p16', 'p17')


class Bundle:
    def __init__(self, repo, name):
        src = SOURCES[name]
        raw = subprocess.check_output(['git', '-C', str(repo), 'show', src['commit'] + ':' + src['path']])
        if sha256(raw).hexdigest() != src['sha256']:
            raise ValueError('archive checksum differs: ' + name)
        self.at = None
        if 'committed_at' in src:
            date = subprocess.check_output(['git', '-C', str(repo), 'show', '-s', '--format=%cI', src['commit']], text=True).strip()
            self.at = aware(date)
            if self.at != aware(src['committed_at']):
                raise ValueError('archive commit clock differs: ' + name)
        # Read members in memory, without unpacking vendor data into the source tree.
        self.files = {}
        with tarfile.open(fileobj=BytesIO(raw)) as tar:
            for m in tar:
                if m.isfile() and m.name.endswith('.json'):
                    self.files[m.name] = tar.extractfile(m).read()
        self.raw = {sha256(v).hexdigest(): v for v in self.files.values()}

    def value(self, name):
        return json.loads(self.files[name])

    def freezes(self):
        for name in self.files:
            if 'issued-analyses/' in name or '/freezes/' in name or name.startswith('freezes/'):
                value = self.value(name)
                if isinstance(value, dict) and 'record_sha256' in value:
                    yield verify(value)


def team_name(s):
    s = ALIASES.get(s, s).casefold()
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if c.isalnum())


def same_fixture(first, other):
    keys = ('match_id', 'competition', 'cohort', 'stage', 'home', 'away')
    return (all(first[k] == other.get(k) for k in keys)
            and aware(first['kickoff_at']) == aware(other['kickoff_at']))


def frozen_nations_roster(repo):
    arc = Bundle(repo, 'pairs')
    rows = {}
    for r in arc.freezes():
        if r['track'] != 'nations-paired-candidates':
            continue
        f = require(r['fixture'])
        t = r['payload']['test']
        mid, tier = f['match_id'], f['league_level']
        evidence = f['phase_evidence']
        header = json.loads(arc.raw[evidence['source_sha256']])['header']
        game, = header['competitions']
        names = {}
        for side in ('home', 'away'):
            team, = [x['team'] for x in game['competitors'] if x['homeAway'] == side]
            if 'espn:' + str(team['id']) != f[side]:
                raise ValueError('prematch national team ID mismatch')
            names[side] = {team_name(s) for s in [f[side+'_name'], team.get('shortDisplayName'),
                                                  team.get('displayName'), team.get('name')] if s}
        artifact = json.loads(arc.raw[t['artifact_sha256']])
        if (mid in rows or r['match_id'] != mid or r['revision'] != 1 or r['stage'] != 'prematch'
                or r['evaluation_slot'] != 'FIRST_PREMATCH' or f['identity_verified'] is not True
                or f['cohort'] != f'uefa.nations:{tier}:league-phase' or tier not in ('A','B','C','D')
                or f['stage'] != 'league-phase' or f['score_basis'] != 'regulation_90'
                or f['gender'] != 'men' or f['age'] != 'senior' or f['entity'] != 'national'
                or str(header['id']) != mid.removeprefix('espn:') or header['league']['slug'] != 'uefa.nations'
                or header['season']['year'] != 2026 or 'League Phase' not in header['season']['name']
                or game['status']['type']['state'] != 'pre' or aware(game['date']) != aware(f['kickoff_at'])
                or not aware(evidence['query_time']) <= aware(f['cutoff_at']) <= aware(r['recorded_at'])
                       <= arc.at < aware(f['kickoff_at'])
                or artifact['cohort'] != f['cohort'] or artifact['role'] != 'test'
                or artifact['version'] != t['version'] or aware(artifact['created_at']) > aware(f['cutoff_at'])
                or t['input'] != f or t['matrix_hash'] != digest(t['matrix'])):
            raise ValueError('invalid original Nations freeze ' + mid)
        base = _matrix(t['matrix'])
        picks = score_picks(base)
        if (picks['primary'] != t['primary']
                or {'home':'主胜', 'draw':'平局', 'away':'客胜'}[picks['wdl']] != t['wdl_reference']):
            raise ValueError('recomputed baseline differs from the issued Test decision ' + mid)
        rows[mid] = dict(f=f, tier=tier, date=f['kickoff_at'][:10], names=names, base=base,
                         pair_sha=r['record_sha256'], cutoff=f['cutoff_at'], issue_sha=None,
                         book=None, line=None, market=None, gap='NO_ORIGINAL_COMPREHENSIVE_CUTOFF')
    if len(rows) != 52:
        raise ValueError('original Nations roster changed')
    return rows


def original_pairs(repo):
    rows = frozen_nations_roster(repo)
    r7 = Bundle(repo, 'r7')
    issues = [verify(r) for n in r7.files if (r := r7.value(n)).get('track') == 'research-prematch-continuation-20261123']
    if len(issues) != 192 or len({r['match_id'] for r in issues}) != 192 or set(rows) & {r['match_id'] for r in issues}:
        raise ValueError('protected R7 identity or count differs')
    return rows


def the_odds_inputs(arc, row, fixture, quote):
    """Match the archived cross-provider fixture and both signed prices without fuzzy names."""
    h = quote['source_sha256']
    matches = [n for n, raw in arc.files.items() if n.endswith('/odds.json') and raw == arc.raw.get(h)]
    if not matches:
        raise ValueError('ORIGINAL_ODDS_BYTES_MISSING')
    captures = []
    for name in matches:
        meta = str(Path(name).with_name('source-metadata.json'))
        if meta in arc.files:
            stamp = arc.value(meta).get('odds', {})
            end = stamp.get('response_read_complete_at') or stamp.get('captured_at')
            if (end and stamp.get('sha256') == h and stamp.get('url') == quote['source_url']
                    and aware(stamp.get('captured_at', end)) == aware(end) == aware(quote['query_time'])
                    and aware(stamp['requested_at']) <= aware(end) <= aware(fixture['cutoff_at'])):
                captures.append(name)
    if not captures or urlsplit(quote['source_url']).hostname != 'api.the-odds-api.com':
        raise ValueError('CAPTURE_RECEIPT_MISSING')
    events = [e for e in arc.value(captures[0]) if e['sport_key'] == 'soccer_uefa_nations_league'
              and aware(e['commence_time']) == aware(fixture['kickoff_at'])
              and all(team_name(e[side+'_team']) in row['names'][side] for side in ('home','away'))]
    if len(events) != 1:
        raise ValueError('AMBIGUOUS_OR_MISSING_SIDED_PROVIDER_EVENT')
    event = events[0]
    if quote.get('provider_match_id', event['id']) != event['id']:
        raise ValueError('FROZEN_PROVIDER_ID_CONFLICT')
    books = [b for b in event['bookmakers'] if b['key'] == quote['bookmaker']]
    if len(books) != 1:
        raise ValueError('ORIGINAL_BOOK_MISSING')
    book, = books
    approved = []
    for snap in fixture.get('market_snapshots', []):
        if snap.get('source_sha256') != h or snap['bookmaker'] != book['key']:
            continue
        if snap.get('provider_match_id', event['id']) != event['id']:
            raise ValueError('FROZEN_PROVIDER_ID_CONFLICT')
        if snap['market'] not in ('1x2', 'totals'):
            continue
        is_wdl = snap['market'] == '1x2'
        sides = {event['home_team']:'home', 'Draw':'draw', event['away_team']:'away'} if is_wdl else {'Over':'over','Under':'under'}
        for market in book['markets']:
            if market['key'] != ('h2h' if is_wdl else 'totals'):
                continue
            prices = [x for x in market['outcomes'] if is_wdl or x.get('point') == quote['line']]
            if (len(prices) == len(sides) and {x['name'] for x in prices} == set(sides)
                    and {sides[x['name']]: x['price'] for x in prices} == snap['decimal_odds']
                    and aware(market.get('last_update') or book['last_update']) == aware(snap['quoted_at'])):
                approved.append(dict(snap, provider_match_id=event['id']))
    f = dict(fixture, market_snapshots=approved)
    paired, reason = _paired_capture(f, quote)
    # A separately certified primary total remains scoreable even with no 1X2 expert.
    actual = any(x['market'] == 'totals' and x['line'] == quote['line'] and x['match_id'] == fixture['match_id']
                 and aware(x['query_time']) == aware(quote['query_time']) and aware(x['quoted_at']) == aware(quote['quoted_at'])
                 and x['decimal_odds'] == {'over':quote['over_odds'],'under':quote['under_odds']} for x in approved)
    if not actual:
        raise ValueError('ORIGINAL_TWO_SIDED_LINE_UNCONFIRMED')
    if book['key'] != 'pinnacle':
        return None, 'OTHER_ORIGINAL_BOOK'
    if paired is None:
        return None, reason
    total = dict(line=quote['line'], **paired['total']['decimal_odds'])
    _, grid = quote_only_matrices(paired['h2h']['decimal_odds'], total)
    return grid, None


def nova_line(arc, row, fixture, quote):
    """Nov 17's separate book can supply its original total only."""
    response = json.loads(arc.raw[quote['source_sha256']])
    eid = response['event_id']
    stamp = arc.value(f'markets/{eid}-meta.json')
    if (stamp['http_status'] != 200 or stamp['sha256'] != quote['source_sha256'] or response['stale'] is not False
            or stamp['provider_event_id'] != eid or 'espn:'+str(stamp['espn_event_id']) != fixture['match_id']
            or stamp['url'] != quote['source_url'] or urlsplit(stamp['url']).hostname != 'api.infersports.dev'
            or aware(stamp['query_time_utc']) != aware(quote['query_time'])
            or not aware(quote['quoted_at']) <= aware(response['as_of']) <= aware(quote['query_time'])
            or aware(stamp['kickoff_utc']) != aware(fixture['kickoff_at'])):
        raise ValueError('ORIGINAL_NOVA_RECEIPT_CONFLICT')
    identities = []
    for name in arc.files:
        if not name.startswith('markets/events') or not name.endswith('-raw.json'):
            continue
        meta = arc.value(name.replace('-raw.json','-meta.json'))
        if (meta.get('http_status') != 200 or meta['sha256'] != sha256(arc.files[name]).hexdigest()
                or aware(meta['query_time_utc']) > aware(fixture['cutoff_at'])):
            continue
        identities.extend(e for e in arc.value(name)['data'] if e['id'] == eid)
    if (len(identities) != 1 or identities[0]['league']['name'] != 'UEFANL' + row['tier']
            or identities[0]['sport'] != 'football' or identities[0]['status'] != 'scheduled'
            or aware(identities[0]['scheduled_at']) != aware(fixture['kickoff_at'])
            or not all(team_name(identities[0][s+'_team']['name']) in row['names'][s] for s in ('home','away'))):
        raise ValueError('ORIGINAL_NOVA_IDENTITY_CONFLICT')
    found = [q for q in response['odds'] if q['bookmaker'] == 'nova88' and q['period'] == 'full_time'
             and q['market_type'] == 'totals' and q['format'] == 'decimal' and q['status'] == 'open'
             and q['line'] == quote['line'] and q['prices'] == {'over':quote['over_odds'],'under':quote['under_odds']}
             and aware(q['as_of']) == aware(quote['quoted_at'])]
    if len(found) != 1:
        raise ValueError('ORIGINAL_NOVA_LINE_CONFLICT')


def merge_original_markets(repo, rows):
    for key in PREMATCH:
        arc = Bundle(repo, key)
        for r in arc.freezes():
            mid = r['match_id']
            if (mid not in rows or r['stage'] != 'prematch' or r['track'] != 'comprehensive'
                    or r['revision'] != 1 or r['evaluation_slot'] != 'FIRST_PREMATCH'):
                continue
            row, f = rows[mid], r['fixture']
            if not same_fixture(row['f'], f):
                raise ValueError('original comprehensive identity mismatch ' + mid)
            if not aware(row['f']['cutoff_at']) <= aware(f['cutoff_at']) <= aware(r['recorded_at']) <= arc.at < aware(f['kickoff_at']):
                continue
            if row['issue_sha']:
                if row['issue_sha'] != r['record_sha256']:
                    raise ValueError('conflicting original comprehensive records ' + mid)
                continue
            row.update(issue_sha=r['record_sha256'], cutoff=f['cutoff_at'], gap='NO_ORIGINAL_QUOTE')
            row['signed_primary_present'] = r['payload'].get('primary') is not None
            q = r['payload'].get('actual_quote')
            if not q or q != f.get('total_quote'):
                continue
            # The provider calls its full-game market 'totals'. The main market
            # bridge serializes the same market as 'standard_total'.
            audited_fixture = f
            if (q.get('market') == 'totals' and q.get('period') == 'regulation_90'
                    and urlsplit(q['source_url']).hostname == 'api.the-odds-api.com'):
                audited_fixture = dict(f, total_quote=dict(q, market='standard_total'))
            audit = quote_audit(audited_fixture, max_age_seconds=3600)
            if not audit['eligible']:
                row['gap'] = '+'.join(audit['reasons'])
                continue
            try:
                if q['bookmaker'] == 'nova88':
                    nova_line(arc, row, f, q)
                    market, gap = None, 'OTHER_ORIGINAL_BOOK'
                else:
                    market, gap = the_odds_inputs(arc, row, f, q)
            except ValueError as exc:
                row['gap'] = str(exc)
                continue
            row.update(line=q['line'], book=q['bookmaker'], market=market, gap=gap,
                       raw_sha=q['source_sha256'], quote_age_s=(aware(f['cutoff_at'])-aware(q['quoted_at'])).total_seconds())
    return rows


def inputs(repo):
    return merge_original_markets(repo, original_pairs(repo))
