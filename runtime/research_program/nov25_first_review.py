"""Offline ESPN acquisition, kickoff and regulation-result checks for the November 25 J1/AFC firsts."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from research.analysis_freeze import aware
from .display_window_review import summary_kickoff
from .kpi_validation import _actual
from .three_arm_review import _certify


STAGES = {
    'jpn.1': (14287, 'regular-season', '2026-27 Japanese J1 League, Regular Season', {'regular', 'Japanese J1 League'}),
    'afc.champions': (14524, 'league-stage', '2026-27 AFC Champions League Elite, League Stage',
                      {'league_phase', 'AFC Champions League Elite, East Region'}),
}
REGISTRY_SHA = 'dfa9ef6ac66cd19d57db0965977100a95c64a003cea7d457614f57615bdba7da'


def file_in(root, name):
    path = Path(name)
    dest = (root / path).resolve()
    if path.is_absolute() or '..' in path.parts or not dest.is_relative_to(root.resolve()):
        raise ValueError('acquisition path outside archive')
    return dest


def same_fixture(a, b):
    code = a['cohort'].split(':')[0]
    if (code not in STAGES or b['cohort'].split(':')[0] != code
            or a['stage'] not in STAGES[code][3] or b['stage'] not in STAGES[code][3]
            or a['home'] == a['away'] or any(a[k] != b[k] for k in ('match_id', 'home', 'away'))
            or aware(a['kickoff_at']) != aware(b['kickoff_at'])):
        raise ValueError('original league, stage, kickoff or ordered teams differ: ' + a['match_id'])


def registration(root, fixtures):
    body = (root/'registration.json').read_bytes()
    if sha256(body).hexdigest() != REGISTRY_SHA:
        raise ValueError('different November 25 live intake registration')
    registered = json.loads(body)
    events = registered['events']
    if len(events) != 9 or set(fixtures) != {e['mid'] for e in events}:
        raise ValueError('live acquisition does not cover the nine original firsts')
    for e in events:
        code = e['league_slug']
        stage_id, slug, _, _ = STAGES[code]
        if ('espn:' + e['event_id'] != e['mid'] or e['stage']['season_type_id'] != stage_id
                or e['stage'].get('season_type_slug', e['stage'].get('season_slug')) != slug):
            raise ValueError('registered provider event or stage differs')
        same_fixture(fixtures[e['mid']], dict(match_id=e['mid'], cohort=code, stage=fixtures[e['mid']]['stage'],
                     home='espn:' + e['home']['espn_id'], away='espn:' + e['away']['espn_id'],
                     kickoff_at=e['scheduled_kickoff_at_utc']))
    return registered


def dated_log(path, at):
    data = path.read_bytes()
    result, retained = [], []
    for i, line in enumerate(data.splitlines(keepends=True)):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            if not line.endswith(b'\n') and not data.endswith(b'\n'):
                break  # A writer is appending its last acquisition.
            raise
        stamp = aware(row['captured_at_utc'])
        if stamp <= at:
            result.append((i + 1, row))
            retained.append(line)
    return result, b''.join(retained)


def refresh_log(root, at, *, with_bytes=False):
    result, body = dated_log(root/'refreshes.jsonl', at)
    return (result, body) if with_bytes else result


def _endpoint(row):
    u = urlsplit(row['url'])
    kind, code = row['kind'], row['league_slug']
    q = parse_qs(u.query, keep_blank_values=True)
    if (code not in STAGES or kind not in ('summary', 'scoreboard') or u.scheme != 'https'
            or u.hostname not in ('site.api.espn.com', 'site.web.api.espn.com') or u.username or u.password
            or u.port not in (None, 443) or u.path != f'/apis/site/v2/sports/soccer/{code}/{kind}'
            or (kind == 'summary' and q.get('event') != [str(row['event_id'])])
            or (kind == 'scoreboard' and q.get('dates') != ['20261125'])):
        raise ValueError('unbound soccer acquisition endpoint: ' + row['url'])


def _game(event, fixture, summary):
    mid = fixture['match_id']
    code = fixture['cohort'].split(':')[0]
    game, = event['competitions']
    season = event['season']
    stage_id, slug, name, _ = STAGES[code]
    sides = {c['homeAway']: c for c in game['competitors']}
    if ('espn:' + str(event['id']) != mid or str(game['id']) != str(event['id'])
            or season['type'] != stage_id or season['year'] != 2026
            or (season.get('name') != name if summary else season.get('slug') != slug)
            or (summary and event['league']['slug'] != code) or aware(game['date']) != aware(fixture['kickoff_at'])
            or len(game['competitors']) != 2 or set(sides) != {'home','away'}
            or any('espn:' + str(c['team']['id']) != fixture[s] for s, c in sides.items())):
        raise ValueError('raw ESPN event, stage, schedule or side mismatch: ' + mid)
    if code == 'afc.champions':
        group = game.get('groups' if summary else 'group') or {}
        if (str(group.get('id' if summary else 'groupId')) != '1'
                or (not summary and group.get('name') != 'East Region')):
            raise ValueError('AFC East Region differs from the original fixture: ' + mid)
    return game, sides


def _score(value):
    if isinstance(value, dict):
        value = value.get('displayValue')
    if type(value) is int and value >= 0 or isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    return None


def _observation(body, doc, fixture, game, sides, ref, root, at):
    st = game['status']
    status = st['type']
    score = [_score(sides[s].get('score')) for s in ('home', 'away')]
    item = dict(source=ref, status=status['name'], state=status['state'], completed=status['completed'],
                period=st.get('period'), display_clock=st.get('displayClock'), observed_score=score,
                actual_kickoff_at=None)
    if ref['kind'] != 'summary':
        return item, None
    stamp = ref['captured_at_utc']
    observed = aware(stamp)
    keys = doc.get('keyEvents') or []
    has_start = any(e.get('type', {}).get('type') == 'kickoff' and e.get('period', {}).get('number') == 1 for e in keys)
    if has_start:
        try:
            provider_fixture = dict(fixture, cohort=ref['league_slug'], competition=ref['league_slug'])
            when = summary_kickoff(body, ref['url'], provider_fixture, observed)
            item['actual_kickoff_at'] = when.isoformat()
        except (KeyError, ValueError, TypeError) as exc:
            item['kickoff_issue'] = str(exc)
    if status['name'] != 'STATUS_FULL_TIME' or status['state'] != 'post' or status['completed'] is not True:
        return item, None
    try:
        if (None in score or any(e.get('shootout') is True or (e.get('period', {}).get('number') or 0) > 2 for e in keys)
                or any(s.get('shootoutScore') not in (None, '', '0', 0) for s in sides.values())):
            raise ValueError('ambiguous score, extra periods or shootout')
        for s, goals in zip(('home','away'), score, strict=True):
            halves = sides[s].get('linescores')
            if halves is not None and (len(halves) != 2 or any(_score(v) is None for v in halves)
                                       or sum(_score(v) for v in halves) != goals):
                raise ValueError('regulation halves disagree with the displayed final')
        if item['actual_kickoff_at'] and observed < aware(item['actual_kickoff_at']) + timedelta(minutes=90):
            raise ValueError('FT captured before 90 minutes from actual kickoff')
        raw = dict(path=ref['path'], sha256=ref['sha256'], captured_at=stamp, source_url=ref['url'])
        row = dict(fixture, home_goals=score[0], away_goals=score[1], event_status='FT', score_basis='regulation_90',
                   known_at=stamp, source_url=ref['url'], raw_reference=raw)
        proof = _certify(row, fixture, root, at)
        label = dict(fixture, home_goals=proof['home'], away_goals=proof['away'], event_status='FT', score_basis='regulation_90',
                     observed_at=stamp, evidence=dict(file=ref['path'], sha256=ref['sha256'], url=ref['url'],
                                                       query_time=stamp, identity_verified=True))
        if _actual(label, fixture, root, at) != tuple(score):
            raise ValueError('certified goal values differ')
        return item, label
    except (ValueError, KeyError, TypeError) as exc:
        item['ft_issue'] = str(exc)
        return item, None


def inspect(root, fixtures, captures, at):
    root = root.resolve()
    by_id = {mid: [] for mid in fixtures}
    finals = defaultdict(list)
    files, errors = {}, []
    for _, row in captures:
        captured = aware(row['captured_at_utc'])
        requested = aware(row['requested_at_utc'])
        if not requested <= captured <= at:
            raise ValueError('invalid acquisition clocks')
        if row.get('method', row.get('http_method')) != 'GET':
            raise ValueError('unexpected acquisition method')
        _endpoint(row)
        if row['http_status'] != 200:
            errors.append(dict(url=row['url'], http_status=row['http_status'], captured_at=row['captured_at_utc']))
            if not row.get('path'):
                continue
        path = Path(row['path'])
        if path.parent == Path('raw/refreshes'):
            key = str(row['event_id']) if row['kind'] == 'summary' else 'board'
            suffix = f"-{row['league_slug']}-{row['kind']}-{key}.json"
            if not path.name.endswith(suffix):
                raise ValueError('raw acquisition filename differs from its endpoint')
            wrote = datetime.strptime(path.name[:-len(suffix)], '%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc)
            if not requested <= wrote <= captured:
                raise ValueError('raw file timestamp outside its acquisition interval')
        body = file_in(root, row['path']).read_bytes()
        if len(body) != row['bytes'] or sha256(body).hexdigest() != row['sha256']:
            raise ValueError('acquisition bytes or SHA-256 differ: ' + row['path'])
        if files.setdefault(row['path'], row['sha256']) != row['sha256']:
            raise ValueError('acquisition path reused for different bytes')
        if row['http_status'] != 200:
            continue
        doc = json.loads(body)
        summary = row['kind'] == 'summary'
        if summary:
            events = [doc['header']]
            if str(events[0]['id']) != str(row['event_id']) or 'espn:' + str(row['event_id']) not in fixtures:
                raise ValueError('wrong summary for the registered event')
        else:
            if [g['slug'] for g in doc['leagues']] != [row['league_slug']]:
                raise ValueError('scoreboard league differs')
            events = doc['events']
        ids = [str(e['id']) for e in events]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate provider event in one capture')
        ref = {k: row[k] for k in ('kind', 'league_slug', 'url', 'requested_at_utc', 'captured_at_utc', 'path', 'sha256')}
        for event in events:
            mid = 'espn:' + str(event['id'])
            if mid not in fixtures:
                continue
            fixture = fixtures[mid]
            if row['league_slug'] != fixture['cohort'].split(':')[0]:
                raise ValueError('capture league differs from registered event')
            game, sides = _game(event, fixture, summary)
            item, label = _observation(body, doc, fixture, game, sides, ref, root, at)
            if summary and row.get('status') and row['status'] != item['status']:
                item['parse_discrepancy'] = row['status']
            by_id[mid].append(item)
            if label:
                finals[mid].append(label)
    status_rows, accepted = {}, {}
    for mid, items in by_id.items():
        items.sort(key=lambda r: (aware(r['source']['captured_at_utc']), r['source']['kind'] == 'summary', r['source']['path']))
        by_kind = {v['source']['kind']: v for v in items}
        starts = [v for v in items if v['actual_kickoff_at']]
        distinct = {aware(v['actual_kickoff_at']) for v in starts}
        start = starts[0] if len(distinct) == 1 else None
        labels = sorted(finals[mid], key=lambda r: (aware(r['observed_at']), r['evidence']['file']))
        issues = sorted({v['ft_issue'] for v in items if v.get('ft_issue')})
        if len(distinct) > 1:
            issues.append('conflicting first-period kickoff wallclocks')
        if len({(v['home_goals'], v['away_goals']) for v in labels}) > 1:
            issues.append('conflicting certified FT90 scores')
        elif labels and len(distinct) <= 1:
            latest = by_kind['summary']
            if latest['status'] != 'STATUS_FULL_TIME' or latest.get('ft_issue') or latest['completed'] is not True or latest['state'] != 'post':
                issues.append('subsequent summary withdrew or failed to certify the final')
            elif start and aware(labels[0]['observed_at']) < aware(start['actual_kickoff_at']) + timedelta(minutes=90):
                issues.append('result precedes actual kickoff plus 90 minutes')
            else:
                accepted[mid] = labels[0]
        status_rows[mid] = dict(fixture=fixtures[mid], captures=len(items), latest=items[-1] if items else None,
                                latest_by_kind=by_kind, kickoff=start and dict(at=start['actual_kickoff_at'], source=start['source']),
                                issues=issues)
    return status_rows, accepted, files, errors


def seats(ids, picks, actuals):
    if len(ids) > 3 or len(set(ids)) != len(ids):
        raise ValueError('invalid original three-seat list')
    rows = []
    for i, mid in enumerate(ids, 1):
        p = picks[mid]
        score = actuals.get(mid)
        actual = '-'.join(map(str, score)) if score is not None else None
        rows.append(dict(seat=i, match_id=mid, primary=p['primary'], top3=p['top3'], actual=actual,
                         first_hit=actual == p['primary'] if actual else None, top3_hit=actual in p['top3'] if actual else None))
    settled = [r for r in rows if r['actual'] is not None]
    hits = sum(r['first_hit'] for r in settled)
    return dict(planned=3, occupied=len(rows), empty=3-len(rows), settled=len(settled), first_hits=hits,
                top3_hits=sum(r['top3_hit'] for r in settled), short=len(rows)<3, seats=rows,
                all_three_first_sweep=(hits == 3 if len(settled) == 3 else None))


# Public match code -> ESPN event and the ordered (ESPN club, J.LEAGUE club, official slug).
J1_CARDS = {
    '112501': ('401877530', ('131701', '176', 'mito'), ('3393', '123', 'tokyov')),
    '112502': ('401877529', ('7111', '121', 'chiba'), ('7108', '127', 'nagoya')),
    '112503': ('401877546', ('3384', '270', 'ftokyo'), ('7114', '129', 'hiroshima')),
    '112504': ('401877528', ('22522', '30314', 'okayama'), ('7104', '126', 'shimizu')),
    '112505': ('401877527', ('7107', '135', 'fukuoka'), ('7116', '124', 'yokohamafm')),
    '112506': ('401877545', ('19001', '30531', 'nagasaki'), ('7112', '86', 'kawasakif')),
    '112507': ('401877547', ('3385', '122', 'urawa'), ('7109', '133', 'cosaka')),
}
JST = ZoneInfo('Asia/Tokyo')
J1_URL = 'https://www.jleague.jp/match/j1/2026/{}/'
J1_ROUND = '第16節'


class _MatchCard(HTMLParser):
    """Read the rendered match header and its own public serialized game-details component."""
    void = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
    prefix = 'o-page-header__'

    def __init__(self):
        super().__init__()
        self.stack = []
        self.fields = defaultdict(list)
        self.states = []
        self.canonical, self.og_url, self.og_title = [], [], []
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'link' and 'canonical' in a.get('rel', '').split():
            self.canonical.append(a.get('href'))
        if tag == 'meta':
            if a.get('property') == 'og:url':
                self.og_url.append(a.get('content'))
            elif a.get('property') == 'og:title':
                self.og_title.append(a.get('content'))
        if tag in self.void:
            return
        parent = self.stack[-1][1] if self.stack else frozenset()
        classes = a.get('class', '').split()
        scope = parent.union(c for c in classes if c.startswith('o-page-header'))
        # Next streams the real header in a hidden insertion target. Its loading skeleton has its own class.
        if any('skeleton' in c for c in classes) or ('o-page-header--game-details' in scope and a.get('aria-hidden') == 'true'):
            scope = scope.union({'hidden'})
        self.stack.append((tag, scope))
        if 'o-page-header--game-details' in classes and 'hidden' not in scope:
            self.states.extend(c.removeprefix('o-page-header--game-details--') for c in classes
                               if c.startswith('o-page-header--game-details--'))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.cdata_elem == 'script':
            prefix = 'self.__next_f.push('
            expr = data.strip().removesuffix(';')
            if expr.startswith(prefix) and expr.endswith(')'):
                chunk = json.loads(expr[len(prefix):-1])
                if len(chunk) == 2 and chunk[0] == 1 and isinstance(chunk[1], str):
                    self.chunks.append(chunk[1])
            return
        value = data.strip()
        if not value or not self.stack:
            return
        scope = self.stack[-1][1]
        if 'hidden' in scope or 'o-page-header--game-details' not in scope:
            return
        for field in ('date', 'section', 'match-score'):
            if self.prefix + field in scope:
                self.fields[field].append(value)
        if self.prefix + 'match-info' in scope:
            self.fields['match-info'].append(value)
        if self.prefix + 'club-name' in scope:
            for side in ('home','away'):
                if self.prefix + 'club--' + side in scope:
                    self.fields[side].append(value)

    def game(self):
        games = []
        def visit(node):
            if isinstance(node, dict):
                if node.get('variant') == 'game-details' and 'homeTeam' in node and 'awayTeam' in node:
                    games.append(node)
                else:
                    for child in node.values():
                        visit(child)
            elif isinstance(node, list):
                for child in node:
                    visit(child)
        decoder = json.JSONDecoder()
        for line in ''.join(self.chunks).splitlines():
            if '"game-details"' not in line or '"homeTeam"' not in line:
                continue
            start = re.match(r'^[0-9a-f]+:(?=[\[{])', line)
            if start:
                visit(decoder.raw_decode(line[start.end():])[0])
        if len(games) != 1:
            raise ValueError('expected one structured official match header')
        return games[0]


def _official_page(body):
    page = _MatchCard()
    page.feed(body.decode('utf-8'))
    page.close()
    return page


def official_binding(body, ref, fixture, names):
    """Bind a registered J1 fixture to its official, verified pregame match card."""
    day = aware(fixture['kickoff_at']).astimezone(JST).date()
    code = ref['code']
    expected = rf'https://www\.jleague\.jp/match/j1/{day.year}/({day:%m%d}[0-9]{{2}})/'
    route = re.fullmatch(expected, ref['url'])
    if not route or route[1] != code or aware(ref['captured_at_utc']) >= aware(fixture['kickoff_at']):
        raise ValueError('expected the independently paired official pregame card')
    game = _official_page(body).game()
    clubs = {}
    for side in ('home', 'away'):
        raw = game[side + 'Team']
        if (raw['name'] != names[side] or not str(raw['teamId']).isascii() or not str(raw['teamId']).isdecimal()
                or not re.fullmatch(r'[a-z0-9-]+', raw['code'])):
            raise ValueError('the official team differs from the pregame schedule')
        clubs[side] = [fixture[side].removeprefix('espn:'), str(raw['teamId']), raw['code']]
    if clubs['home'][1] == clubs['away'][1]:
        raise ValueError('official home and away identities coincide')
    if not re.fullmatch(r'第[0-9]+節', game['section']):
        raise ValueError('expected an official regular-season matchday')
    identity = {key: fixture[key] for key in ('match_id', 'kickoff_at', 'cohort', 'stage', 'home', 'away')}
    binding = dict(fixture=identity, url=ref['url'], code=code, section=game['section'], names=names, **clubs)
    proof = official_card(body, ref, fixture, binding=binding)
    kickoff = aware(fixture['kickoff_at'])
    interval = proof['ko_interval']
    info = proof['page_match_info']
    clocks = {part for part in info if re.fullmatch(r'[0-9]{2}:[0-9]{2}', part)}
    displayed = (before_official_minute(fixture['kickoff_at'], interval) == 'inside_interval_unproved' if interval
                 else 'KICK OFF' in info and clocks == {kickoff.astimezone(JST).strftime('%H:%M')})
    if proof['state'] != 'scheduled' or aware(proof['structured_date_utc']) != kickoff or not displayed:
        raise ValueError('official pregame status or planned time differs')
    return binding


def official_card(body, ref, fixture, *, binding=None):
    code = ref['code']
    if binding is None:
        eid, home, away = J1_CARDS[code]
        url, section = J1_URL.format(code), J1_ROUND
    else:
        eid = fixture['match_id'].removeprefix('espn:')
        home, away = tuple(binding['home']), tuple(binding['away'])
        url, section = binding['url'], binding['section']
        keys = ('match_id', 'kickoff_at', 'cohort', 'stage', 'home', 'away')
        if (binding['fixture'] != {key: fixture[key] for key in keys} or binding['code'] != code
                or not re.fullmatch(r'https://www\.jleague\.jp/match/j1/[0-9]{4}/[0-9]{6}/', url)
                or not re.fullmatch(r'第[0-9]+節', section)):
            raise ValueError('official card does not match its original fixture binding')
    if (ref['url'] != url or fixture['match_id'] != 'espn:'+eid or fixture['cohort'] != 'jpn.1'
            or fixture['stage'] not in STAGES['jpn.1'][3]
            or (fixture['home'], fixture['away']) != ('espn:'+home[0], 'espn:'+away[0])):
        raise ValueError('wrong official URL or registered ESPN identity')
    page = _official_page(body)
    if page.canonical != [url] or page.og_url != [url]:
        raise ValueError('official canonical URL differs from the acquisition')
    g = page.game()
    fields = page.fields
    if (g['tournament'] != 'j1' or g['tournamentName'] != '明治安田Ｊ１リーグ'
            or fields['section'] != [section] or g['section'] != section or page.states != [g['type']]):
        raise ValueError('official J1 league, round or rendered match phase differs')
    teams = {}
    for side, expected in [('home',home),('away',away)]:
        team = g[side+'Team']
        if (str(team['teamId']), team['code']) != expected[1:] or team['name'] not in fields[side]:
            raise ValueError('official ordered club IDs, slugs or Japanese names differ')
        teams[side] = dict(espn_id=expected[0],official_id=expected[1],slug=team['code'],name=team['name'])
    day = aware(fixture['kickoff_at']).astimezone(JST).date()
    title = f"【公式】{g['homeTeam']['nameS']} vs {g['awayTeam']['nameS']}の試合結果・データ（{g['tournamentName']}：{day.year}年{day.month}月{day.day}日）"
    if len(page.og_title) != 1 or not page.og_title[0].startswith(title):
        raise ValueError('official title differs from the registered calendar and teams')
    header, = fields['date']
    parts = re.fullmatch(r'(\d{4}/\d{2}/\d{2}) \(([月火水木金土日])\)(?: (\d{2}:\d{2}) KO)?', header)
    if not parts or parts[1] != day.strftime('%Y/%m/%d') or parts[2] != '月火水木金土日'[day.weekday()]:
        raise ValueError('official Japanese calendar day or weekday differs')
    raw_date = g['date'].removeprefix('$D')
    native = aware(raw_date)
    if not raw_date.endswith('Z') or native.astimezone(JST).date() != day:
        raise ValueError('official structured UTC date differs from the registered calendar')
    info = fields['match-info']
    kind = g['type']
    score = [g[s+'Team'].get('score') for s in ('home','away')]
    scored = all(type(x) is int and x >= 0 for x in score) and fields['match-score'] == list(map(str,score))
    live_phase = g.get('liveMatchInfo') or {}
    extras = ('延長','ＰＫ','PK','中止','中断','延期','没収')
    nonreg = any(x in v for x in extras for v in info + [str(g.get('afterCanceledInfo') or '')])
    nonreg |= any('shootout' in k.lower() or 'penalty' in k.lower() for side in ('homeTeam','awayTeam')
                  for k,v in g[side].items() if v is not None)
    final_text = any(v in ('試合終了','終了') for v in info)
    state = 'reference'
    if kind == 'pre-game':
        state = 'scheduled'
    elif nonreg:
        state = 'nonregulation_or_interrupted'
    elif kind == 'in-game' and not final_text and (live_phase.get('periodOfTime') or 'LIVE' in info):
        state = 'live'
    elif kind == 'post-game' and final_text and 'LIVE' not in info and scored:
        state = 'final'
    interval, qualified, schedule_overlap, reason = None, False, False, 'no explicit KO minute in the official header'
    if parts[3]:
        start = datetime.strptime(parts[1]+' '+parts[3], '%Y/%m/%d %H:%M').replace(tzinfo=JST)
        end = start + timedelta(minutes=1)
        interval = dict(start_inclusive=start.astimezone(timezone.utc).isoformat(timespec='minutes'),
                        end_exclusive=end.astimezone(timezone.utc).isoformat(timespec='minutes'),
                        start_jst=start.isoformat(timespec='minutes'), end_jst=end.isoformat(timespec='minutes'),
                        precision='minute', timezone='Asia/Tokyo')
        if not start <= native < end:
            reason = 'KO label and independent structured page date disagree'
        elif state not in ('live','final'):
            reason = 'actual play unconfirmed on this official page'
        elif aware(ref['captured_at_utc']) < end:
            reason = 'capture precedes the end of the displayed minute'
        elif start <= aware(fixture['kickoff_at']) < end:
            schedule_overlap = True
            reason = 'reported KO coincides with the original scheduled minute and needs actual-start corroboration'
        else:
            qualified, reason = True, None
    return dict(source=ref, code=code, match_id=fixture['match_id'], teams=teams, league=g['tournamentName'],
                round=section, calendar_jst=day.isoformat(), scheduled_utc=fixture['kickoff_at'],
                header_verbatim=header, structured_date_utc=raw_date, page_type=kind, page_match_info=info,
                live_period=live_phase.get('periodOfTime'), state=state, observed_score=score if scored else None,
                ko_interval=interval, ko_eligible=qualified, ko_matches_schedule=schedule_overlap, ko_reference_reason=reason)


def before_official_minute(stamp, interval):
    instant = aware(stamp)
    if instant < aware(interval['start_inclusive']):
        return 'before_entire_interval'
    if instant < aware(interval['end_exclusive']):
        return 'inside_interval_unproved'
    return 'at_or_after_interval'


def official_crosscheck(pages, status, label):
    """Compare independent sources as of the same cutoff. An older live report can precede a genuine FT."""
    latest = pages[-1] if pages else None
    espn_start = status.get('kickoff')
    good = [p for p in pages if p['ko_eligible'] or (p.get('ko_matches_schedule') and espn_start
            and before_official_minute(espn_start['at'],p['ko_interval'])=='inside_interval_unproved')]
    intervals = {p['ko_interval']['start_inclusive'] for p in good}
    interval = good[0]['ko_interval'] if len(intervals) == 1 else None
    issues = []
    if len(intervals) > 1:
        issues.append('conflicting official KO minutes')
    if interval and espn_start and before_official_minute(espn_start['at'],interval) != 'inside_interval_unproved':
        issues.append('ESPN kickoff falls outside the official KO minute')
    if interval and 'conflicting first-period kickoff wallclocks' in status.get('issues',[]):
        issues.append('conflicting ESPN kickoff observations')
    timing_conflict = bool(issues)
    if timing_conflict:
        interval = None
    finals = [p for p in pages if p['state'] == 'final']
    if len({tuple(p['observed_score']) for p in finals}) > 1:
        issues.append('official finals disagree')
    if finals and latest['state'] != 'final':
        issues.append('later official page withdrew the final')
    if finals and label and finals[-1]['observed_score'] != [label['home_goals'],label['away_goals']]:
        issues.append('official and ESPN final scores disagree')
    if label and any(p['state']=='nonregulation_or_interrupted' for p in pages):
        issues.append('official match contains a nonregulation or interrupted status')
    summary = status.get('latest_by_kind',{}).get('summary')
    if (finals and not label and summary and summary['status'] != 'STATUS_FULL_TIME'
            and aware(summary['source']['captured_at_utc']) >= aware(finals[-1]['source']['captured_at_utc'])):
        issues.append('subsequent ESPN summary has not confirmed the official final')
    if interval:
        earliest_certain_ft = aware(interval['end_exclusive']) + timedelta(minutes=90)
        if ((label and aware(label['observed_at']) < earliest_certain_ft)
                or any(aware(p['source']['captured_at_utc']) < earliest_certain_ft for p in finals)):
            issues.append('FT precedes 90 minutes over the whole official KO interval')
    if label and latest and latest['state'] != 'final' and aware(latest['source']['captured_at_utc']) >= aware(label['observed_at']):
        issues.append('official page did not confirm full time at or after the ESPN final')
    if issues:
        verdict = 'pending_conflict'
    elif not label:
        verdict = 'awaiting_certified_espn_ft90'
    elif finals:
        verdict = 'official_and_espn_ft_agree'
    else:
        verdict = 'espn_ft90_only'
    return dict(verdict=verdict, issues=issues, kickoff_conflict=timing_conflict,
                official_pages=len(pages), latest_official=latest and latest['source'],
                official_state=latest and latest['state'], official_score=latest and latest['observed_score'],
                official_final=finals[-1]['source'] if finals else None, espn_certified=label,
                kickoff_interval=interval, kickoff_source=good[0]['source'] if interval else None)
