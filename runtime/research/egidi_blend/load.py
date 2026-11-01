"""Load match and odds rows from JSONL, the canonical SQLite, or football-data.co.uk CSVs.

Asian leagues are accepted only when a row is actually present. This loader
does not call ESPN, does not invent a K League slug, and does not fill odds.
"""
import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from constants import ALL_LEAGUES, EUROPE, FOOTBALL_DATA_DIVISIONS, OOD_ASIA, season_start_for
from market import odds_from_mapping, parse_odd


@dataclass
class Match:
    match_id: str
    league: str
    season: str
    season_start: int
    date: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    neutral: bool = False
    odds_1x2: tuple = None
    odds_1x2_closing: bool = False
    odds_ou: tuple = None
    odds_ou_closing: bool = False
    odds_cs: dict = None
    home_xg: float = None
    away_xg: float = None
    lineup_known: bool = False
    source: str = ''


def season_label(league, season_start):
    if league in EUROPE or league == 'aus.1' or (league == 'jpn.1' and season_start >= 2026):
        return f'{season_start}/{str(season_start + 1)[-2:]}'
    return str(season_start)


def _xg_pair(row):
    for home_key, away_key in (('home_xg', 'away_xg'), ('HxG', 'AxG'), ('HXG', 'AXG')):
        if home_key not in row and away_key not in row:
            continue
        try:
            home = float(row[home_key])
            away = float(row[away_key])
        except (KeyError, TypeError, ValueError):
            continue
        if home == home and away == away and 0 <= home <= 30 and 0 <= away <= 30:
            return home, away
    return None, None


def _apply_parsed(match, parsed, source_xg=None):
    if not parsed:
        return match
    if parsed.get('odds_1x2') and not match.odds_1x2:
        match.odds_1x2 = parsed['odds_1x2']
        match.odds_1x2_closing = bool(parsed.get('odds_1x2_closing'))
    elif parsed.get('odds_1x2') and parsed.get('odds_1x2_closing') and not match.odds_1x2_closing:
        match.odds_1x2 = parsed['odds_1x2']
        match.odds_1x2_closing = True
    if parsed.get('odds_ou') and (not match.odds_ou or (parsed.get('odds_ou_closing') and not match.odds_ou_closing)):
        match.odds_ou = parsed['odds_ou']
        match.odds_ou_closing = bool(parsed.get('odds_ou_closing'))
    if parsed.get('odds_cs') and not match.odds_cs:
        match.odds_cs = parsed['odds_cs']
    if source_xg and source_xg[0] is not None and match.home_xg is None:
        match.home_xg, match.away_xg = source_xg
    return match


def _richness(match):
    return (bool(match.odds_1x2_closing), match.odds_1x2 is not None,
            bool(match.odds_ou_closing), match.odds_ou is not None,
            match.odds_cs is not None, match.home_xg is not None, bool(match.lineup_known))


def parse_fd_date(text):
    parts = str(text).strip().split('/')
    if len(parts) != 3:
        return None
    day, month, year = parts
    fmt = '%d/%m/%Y' if len(year) == 4 else '%d/%m/%y' if len(year) == 2 else None
    if fmt is None:
        return None
    try:
        return datetime.strptime(text.strip(), fmt).date().isoformat()
    except ValueError:
        return None


def _league_from_div_or_name(div, path):
    reverse = {code: league for league, code in FOOTBALL_DATA_DIVISIONS.items()}
    token = (div or '').strip().upper()
    if token in reverse:
        return reverse[token]
    pieces = set(path.stem.upper().replace('-', '_').split('_'))
    for code, league in reverse.items():
        if code in pieces:
            return league
    return None


def matches_from_football_data_file(path):
    path = Path(path)
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    if 'HomeTeam' not in text.splitlines()[0]:
        return []
    found = []
    today = date.today().isoformat()
    for row in csv.DictReader(text.splitlines()):
        league = _league_from_div_or_name(row.get('Div'), path)
        if league is None:
            continue
        kickoff = parse_fd_date(row.get('Date', ''))
        home = (row.get('HomeTeam') or '').strip()
        away = (row.get('AwayTeam') or '').strip()
        if not kickoff or not home or not away or home == away or kickoff > today:
            continue
        try:
            home_goals = int(row['FTHG'])
            away_goals = int(row['FTAG'])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= home_goals <= 30 or not 0 <= away_goals <= 30:
            continue
        season_start = season_start_for(league, kickoff)
        if season_start is None:
            continue
        parsed = odds_from_mapping(row)
        home_xg, away_xg = _xg_pair(row)
        match = Match(
            match_id=f'fd:{league}:{kickoff}:{home}:{away}',
            league=league, season=season_label(league, season_start), season_start=season_start,
            date=kickoff, home=home, away=away, home_goals=home_goals, away_goals=away_goals,
            odds_1x2=parsed['odds_1x2'], odds_1x2_closing=parsed['odds_1x2_closing'],
            odds_ou=parsed['odds_ou'], odds_ou_closing=parsed['odds_ou_closing'],
            odds_cs=parsed['odds_cs'], home_xg=home_xg, away_xg=away_xg,
            source=f'football-data.co.uk:{path.name}')
        found.append(match)
    return found


def load_football_data_dir(directory):
    directory = Path(directory)
    matches = []
    files = []
    if not directory.exists():
        return matches, files
    for path in sorted(directory.rglob('*.csv')):
        rows = matches_from_football_data_file(path)
        if rows:
            files.append(str(path))
            matches.extend(rows)
    return matches, files


def _match_from_record(record, source):
    league = record.get('league') or record.get('competition') or record.get('cohort')
    if league not in ALL_LEAGUES:
        return None
    kickoff = str(record.get('date') or '')[:10]
    home = str(record.get('home') or record.get('home_name') or '').strip()
    away = str(record.get('away') or record.get('away_name') or '').strip()
    if len(kickoff) != 10 or not home or not away or home == away:
        return None
    try:
        home_goals = int(record['home_goals'] if 'home_goals' in record else record['h90'])
        away_goals = int(record['away_goals'] if 'away_goals' in record else record['a90'])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= home_goals <= 30 or not 0 <= away_goals <= 30:
        return None
    if record.get('season_start') is not None:
        season_start = int(record['season_start'])
    else:
        season_start = season_start_for(league, kickoff)
    if season_start is None:
        return None
    parsed = odds_from_mapping(record)
    home_xg, away_xg = _xg_pair(record)
    neutral = record.get('neutral')
    return Match(
        match_id=str(record.get('match_id') or f'{source}:{league}:{kickoff}:{home}:{away}'),
        league=league, season=str(record.get('season') or season_label(league, season_start)),
        season_start=season_start, date=kickoff, home=home, away=away,
        home_goals=home_goals, away_goals=away_goals,
        neutral=bool(neutral) if isinstance(neutral, bool) else False,
        odds_1x2=parsed['odds_1x2'], odds_1x2_closing=parsed['odds_1x2_closing'],
        odds_ou=parsed['odds_ou'], odds_ou_closing=parsed['odds_ou_closing'],
        odds_cs=parsed['odds_cs'], home_xg=home_xg, away_xg=away_xg,
        lineup_known=bool(record.get('lineup_known', False)),
        source=source)


def load_jsonl(path):
    matches = []
    with Path(path).open(encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if isinstance(record, list):
                records = record
            else:
                records = [record]
            for item in records:
                match = _match_from_record(item, f'jsonl:{Path(path).name}')
                if match is not None:
                    matches.append(match)
    return matches


def _resolve(redirects, team_id):
    seen = set()
    while team_id in redirects:
        if team_id in seen:
            break
        seen.add(team_id)
        team_id = redirects[team_id]
    return team_id


def load_sqlite(path, leagues=ALL_LEAGUES):
    """Read-only canonical matches. Odds are taken only from stored raw rows that contain them."""
    path = Path(path)
    uri = path.resolve().as_uri() + '?mode=ro'
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'matches' not in tables or 'teams' not in tables:
        con.close()
        raise ValueError('sqlite is missing matches/teams tables: ' + str(path))
    match_cols = {row[1] for row in con.execute('PRAGMA table_info(matches)')}
    redirects = {}
    if 'team_redirects' in tables:
        redirects = dict(con.execute('SELECT old_id, new_id FROM team_redirects'))
    names = {row['team_id']: row['name'] for row in con.execute('SELECT team_id, name FROM teams')}
    where = ','.join('?' for _ in leagues)
    sql = f'''SELECT match_id, competition, season, date, home_id, away_id, h90, a90
              {', result_status' if 'result_status' in match_cols else ''}
              {', training_eligible' if 'training_eligible' in match_cols else ''}
              {', stage' if 'stage' in match_cols else ''}
              FROM matches WHERE competition IN ({where}) AND h90 IS NOT NULL AND a90 IS NOT NULL'''
    found = []
    index = {}
    for row in con.execute(sql, tuple(leagues)):
        item = dict(row)
        if item.get('result_status') == 'conflict':
            continue
        if 'training_eligible' in item and item['training_eligible'] not in (1, None):
            continue
        stage = item.get('stage')
        if stage not in (None, '', 'regular', 'regular-season'):
            continue
        league = item['competition']
        kickoff = str(item['date'])[:10]
        home_id = _resolve(redirects, item['home_id'])
        away_id = _resolve(redirects, item['away_id'])
        home = names.get(home_id) or names.get(item['home_id'])
        away = names.get(away_id) or names.get(item['away_id'])
        if not home or not away:
            continue
        try:
            season_start = season_start_for(league, kickoff)
        except ValueError:
            continue
        if season_start is None:
            continue
        match = Match(
            match_id=str(item['match_id']), league=league,
            season=str(item.get('season') or season_label(league, season_start)),
            season_start=season_start, date=kickoff, home=home, away=away,
            home_goals=int(item['h90']), away_goals=int(item['a90']),
            source='sqlite:' + path.name)
        found.append(match)
        index[(league, kickoff, home_id, away_id)] = match
        index[(league, kickoff, item['home_id'], item['away_id'])] = match
    if 'observations' in tables and index:
        obs_sql = f'''SELECT competition, date, home_id, away_id, raw_json FROM observations
                      WHERE competition IN ({where}) AND raw_json IS NOT NULL
                      AND (provider='football-data' OR raw_json LIKE '%odds_1x2%'
                           OR raw_json LIKE '%PSCH%' OR raw_json LIKE '%B365H%'
                           OR raw_json LIKE '%HxG%' OR raw_json LIKE '%home_xg%')'''
        for row in con.execute(obs_sql, tuple(leagues)):
            match = index.get((row['competition'], str(row['date'])[:10], row['home_id'], row['away_id']))
            if match is None:
                continue
            try:
                raw = json.loads(row['raw_json'])
            except (TypeError, ValueError):
                continue
            if isinstance(raw, dict):
                _apply_parsed(match, odds_from_mapping(raw), _xg_pair(raw))
                if isinstance(raw.get('neutral'), bool):
                    match.neutral = raw['neutral']
                if raw.get('lineup_known') is True:
                    match.lineup_known = True
    con.close()
    return found


def merge_matches(groups):
    """Earlier groups win on the same league/date/home/away identity. Richer odds replace a poorer twin."""
    chosen = {}
    order = []
    for matches in groups:
        for match in matches:
            key = (match.league, match.date, match.home.casefold(), match.away.casefold())
            current = chosen.get(key)
            if current is None:
                chosen[key] = match
                order.append(key)
            elif _richness(match) > _richness(current):
                chosen[key] = match
    return [chosen[key] for key in order]


def download_public_football_data(dest, start=1993, end=None):
    """Download the public top-five CSVs football-data.co.uk already used by this repo.

    Only E0, SP1, I1, D1 and F1. No ESPN request and no Asian feed.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if end is None:
        end = date.today().year
    report = {'directory': str(dest), 'downloaded': [], 'skipped_existing': [], 'failed': [],
              'divisions': dict(FOOTBALL_DATA_DIVISIONS), 'espn_requested': False}
    for year in range(start, end + 1):
        code = f'{year % 100:02d}{(year + 1) % 100:02d}'
        for league, division in FOOTBALL_DATA_DIVISIONS.items():
            url = f'https://www.football-data.co.uk/mmz4281/{code}/{division}.csv'
            target = dest / f'{year}_{division}.csv'
            if target.exists() and b'HomeTeam' in target.read_bytes()[:800]:
                report['skipped_existing'].append(str(target))
                continue
            request = Request(url, headers={'User-Agent': 'WCPP-research-egidi-blend'})
            try:
                with urlopen(request, timeout=30) as response:
                    body = response.read()
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                report['failed'].append({'url': url, 'error': type(exc).__name__})
                continue
            if b'HomeTeam' not in body[:800]:
                report['failed'].append({'url': url, 'error': 'not_a_football_data_csv'})
                continue
            target.write_bytes(body)
            report['downloaded'].append(str(target))
    return report


def field_coverage(matches):
    by_league = defaultdict(int)
    for match in matches:
        by_league[match.league] += 1
    return {
        'n': len(matches),
        'by_league': dict(sorted(by_league.items())),
        'closing_1x2': sum(bool(m.odds_1x2_closing) for m in matches),
        'any_1x2': sum(m.odds_1x2 is not None for m in matches),
        'closing_ou25': sum(bool(m.odds_ou_closing) for m in matches),
        'any_ou25': sum(m.odds_ou is not None for m in matches),
        'correct_score_odds': sum(m.odds_cs is not None for m in matches),
        'xg': sum(m.home_xg is not None and m.away_xg is not None for m in matches),
        'lineup_known': sum(bool(m.lineup_known) for m in matches),
        'europe_leagues_present': [league for league in EUROPE if by_league[league]],
        'ood_asia_leagues_present': [league for league in OOD_ASIA if by_league[league]],
        'ood_asia_leagues_missing': [league for league in OOD_ASIA if not by_league[league]],
    }
