"""Read the two originally issued Nations research batches at their own clocks.

Only input objects and first issues are opened. The archive index maps original
paths to members; archived paths never resolve against the host filesystem.
"""
from functools import cached_property
from gzip import decompress
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import tarfile
from urllib.parse import urlsplit

import numpy as np

from archive_inputs import same_fixture, team_name
from nations_league.repair import select_score
from research.analysis_freeze import aware, digest, verify
from research_program.future_validation import quote_audit
from research_program.market_baseline import _paired_capture, quote_only_matrices
from research_program.nations_phase import classify_source_phase
from research_program.three_arm_model import _matrix, score_picks, top_three_scores

HERE = Path(__file__).resolve().parent
SOURCES = {
    'repair': ('20261116-nations-repair', 'nations-repair', 'archive-index.json', 'forward-v2'),
    'xg': ('20261117-nations-xg-research', 'nations-xg', 'manifest.json', 'forward'),
}
TRAINING_ROOT = {'repair': 'nations-repair-20261116', 'xg': 'nations-xg-20261117'}
WDL = {'home': '主胜', 'draw': '平局', 'away': '客胜'}


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _sha(raw):
    return sha256(raw).hexdigest()


def _git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


class NativeArchive:
    def __init__(self, repo, panel, spec):
        self.panel = panel
        self.spec = spec
        self.repo = repo
        self.commit = spec['commit']
        directory, stem, index, self.forward = SOURCES[panel]
        prefix = 'workspace-updates/' + directory + '/'
        self.root = 'research/' + TRAINING_ROOT[panel] + '/'
        blob = _git(repo, 'show', self.commit + ':' + prefix + stem + '-evidence.tar.gz')
        _require(_sha(blob) == spec['archive_sha256'], 'native archive digest')
        index_raw = _git(repo, 'show', self.commit + ':' + prefix + index)
        if panel == 'xg':
            _require(_sha(index_raw) == spec['manifest_sha256'], 'xG manifest digest')
        else:
            tree = _git(repo, 'rev-parse', self.commit + ':future/nations-repair-bundle').decode().strip()
            _require(tree == spec['tree'], 'original repair first-issue tree')
        index_data = json.loads(index_raw)
        self.index = {x['original_path'].split('/WCPP/', 1)[1]: x for x in index_data['files']
                      if '/WCPP/' in x['original_path'] and
                      x.get('archive', prefix + stem + '-evidence.tar.gz') == prefix + stem + '-evidence.tar.gz'}
        self.tar = tarfile.open(fileobj=BytesIO(decompress(blob)), mode='r:')
        self.committed_at = _git(repo, 'show', '-s', '--format=%cI', self.commit).decode().strip()

    def raw(self, path, expected=None):
        name = path.split('/WCPP/', 1)[1] if '/WCPP/' in path else self.root + path
        ref = self.index[name]
        raw = self.tar.extractfile(ref['member']).read()
        _require(len(raw) == ref['bytes'] and _sha(raw) == ref['sha256']
                 and (expected is None or expected == ref['sha256']), 'native member digest: ' + name)
        return raw

    def value(self, path, expected=None):
        return json.loads(self.raw(path, expected))

    def issued(self, relative):
        raw = self.raw(self.forward + '/' + relative)
        if self.panel == 'repair':
            direct = _git(self.repo, 'show', self.commit + ':future/nations-repair-bundle/' + relative)
            _require(raw == direct, 'first-issue tree and archive differ')
        return verify(json.loads(raw))

    @cached_property
    def identity_captures(self):
        if self.panel == 'repair':
            lines = self.raw('data-staging/api-captures.jsonl').splitlines()
            return {r['raw_sha256']: (r['request_url'], r['http_request_started'], r['http_read_complete'])
                    for line in lines if (r := json.loads(line)).get('label', '').startswith('future-summary-')
                    and r['http_status'] == 200}
        return {r['raw_sha256']: (r['url'], r['started_at'], r['ended_at'])
                for r in self.value('data/future/captures.json')['requests']
                if r['http_status'] == 200 and r['body_saved']}

    @cached_property
    def markets(self):
        if self.panel == 'repair':
            rc = self.value('data-staging/odds-retry-capture.json')
            _require(rc['stored_raw_is_exact_response_bytes'] and rc['http_status'] == 200, 'repair HTTP receipt')
            captured = (rc['request_url_redacted'], rc['http_request_started'], rc['http_read_complete'])
            mapping = self.value('market-revalidated/market-channel.json')['identity_mappings']
        else:
            rc = self.value('data/markets/capture.json')
            _require(rc['body_saved'] and rc['http_status'] == 200, 'xG HTTP receipt')
            captured = (rc['url'], rc['started_at'], rc['ended_at'])
            mapping = self.value('data/markets/future-matching.json')['rows']
        h = rc['raw_sha256']
        events = self.value(rc['raw_path'], h)
        ids = {r['match_id']: r for r in mapping}
        _require(len(ids) == len(mapping), 'conflicting market crosswalk')
        return captured, h, events, ids


def _validate_fixture(arc, record, f, prior, plan):
    mid = f['match_id']
    cutoff, signed, commit, kickoff = map(aware, (
        f['cutoff_at'], record['recorded_at'], arc.committed_at, f['kickoff_at']))
    ev = f['phase_evidence']
    source = arc.value(ev['source_path'], ev['source_sha256'])
    url, start, end = arc.identity_captures[ev['source_sha256']]
    header = source['header']
    game, = header['competitions']
    grade = classify_source_phase(header)
    teams = {s['homeAway']: 'espn:' + str(s['team']['id']) for s in game['competitors']}
    _require(record['fixture'] == f and record['match_id'] == mid and record['stage'] == 'prematch'
             and record['track'] == plan['payload']['capture_track'] and record['revision'] == 1
             and record['previous_sha256'] is None and record['evaluation_slot'] == 'FIRST_PREMATCH'
             and record['payload']['plan_sha256'] == plan['record_sha256']
             and same_fixture(prior, f) and f['identity_verified'] is True
             and f['gender'] == 'men' and f['age'] == 'senior' and f['entity'] == 'national'
             and f['score_basis'] == 'regulation_90' and f['competition'] == 'uefa.nations'
             and f['stage'] == grade['stage'] == 'league-phase' and grade['complete']
             and f['cohort'] == 'uefa.nations:' + grade['league_level'] + ':league-phase'
             and grade['league_level'] == f['league_level'] == prior['league_level']
             and f['phase'] == 'PreMatch' and header['league']['slug'] == f['competition']
             and header['season']['year'] == 2026 and mid == 'espn:' + str(header['id'])
             and str(header['id']) == str(game['id']) == f['source_event_id']
             and len(game['competitors']) == 2 and teams == {s: f[s] for s in ('home','away')}
             and type(f['neutral']) is bool and game['neutralSite'] is f['neutral'] is prior['neutral']
             and game['status']['type']['state'] == 'pre' and game['status']['type']['completed'] is False
             and aware(game['date']) == kickoff and f['date'] == kickoff.date().isoformat()
             and url == ev['source_url'] == 'https://site.api.espn.com/apis/site/v2/sports/soccer/uefa.nations/summary?event=' + str(game['id'])
             and aware(start) <= aware(end) == aware(ev['query_time']) <= cutoff <= aware(plan['recorded_at'])
             <= signed <= commit < kickoff, 'native first-issue identity, venue, phase or clock: ' + mid)


def _norm(s):
    return team_name(s.replace('&', ' and '))


def _native_market(arc, f, roster_row):
    q = f.get('total_quote')
    audit = quote_audit(f)
    if not audit['eligible']:
        return None, None, None, '+'.join(audit['reasons'])
    (url, start, end), raw_sha, events, ids = arc.markets
    mid = f['match_id']
    cross = ids.get(mid)
    if (not cross or q['bookmaker'] != 'pinnacle' or q['provider'] != 'the-odds-api'
            or q['source_sha256'] != raw_sha or q['source_url'] != url or
            urlsplit(url).hostname != 'api.the-odds-api.com' or
            not aware(start) <= aware(end) == aware(q['query_time']) <= aware(f['cutoff_at'])):
        return None, None, None, 'NATIVE_RECEIPT_OR_CROSSWALK_MISMATCH'
    if arc.panel == 'repair':
        eid = cross['provider_match_id']
        valid_cross = (cross['home_id'] == f['home'] and cross['away_id'] == f['away'] and
                       aware(cross['kickoff_at']) == aware(f['kickoff_at']) and
                       cross['identity_evidence_sha256'] == f['supplemental_phase_evidence']['source_sha256'])
    else:
        eid = cross['market_event_id']
        valid_cross = (cross['source_sha256'] == raw_sha and aware(cross['captured_at']) == aware(end)
                       and aware(cross['commence_time']) == aware(f['kickoff_at']) and cross['identity_match_count'] == 1)
    events = [e for e in events if e['sport_key'] == 'soccer_uefa_nations_league'
              and aware(e['commence_time']) == aware(f['kickoff_at'])
              and all(_norm(e[s+'_team']) in roster_row['names'][s] for s in ('home','away'))]
    if not valid_cross or len(events) != 1 or events[0]['id'] != eid:
        return None, None, None, 'NATIVE_SIDED_EVENT_MISMATCH'
    event, = events
    if arc.panel == 'xg' and any(cross[s] != event[s+'_team'] for s in ('home','away')):
        return None, None, None, 'NATIVE_SIDED_EVENT_MISMATCH'
    books = [b for b in event['bookmakers'] if b['key'] == q['bookmaker']]
    if len(books) != 1:
        return None, None, None, 'ORIGINAL_BOOK_MISSING'
    book, = books
    snaps = []
    for m in book['markets']:
        if m['key'] not in ('h2h','totals'):
            continue
        wdl = m['key'] == 'h2h'
        sides = ({event['home_team']:'home', 'Draw':'draw', event['away_team']:'away'} if wdl
                 else {'Over':'over','Under':'under'})
        offers = [x for x in m['outcomes'] if wdl or x.get('point') == q['line']]
        if len(offers) != len(sides) or {x['name'] for x in offers} != set(sides):
            continue
        snaps.append(dict(match_id=mid, provider_match_id=eid, bookmaker=book['key'],
                          market='1x2' if wdl else 'totals', period='regulation_90', line=None if wdl else q['line'],
                          source_url=url, source_sha256=raw_sha, query_time=end,
                          quoted_at=m.get('last_update') or book['last_update'],
                          decimal_odds={sides[x['name']]: x['price'] for x in offers}))
    totals = [s for s in snaps if s['market'] == 'totals']
    if not totals or any(aware(s['quoted_at']) != aware(q['quoted_at']) or
                         s['decimal_odds'] != {'over':q['over_odds'],'under':q['under_odds']} for s in totals):
        return None, None, None, 'UNVERIFIED_NATIVE_TOTAL'
    pair, gap = _paired_capture(dict(f, market_snapshots=snaps), q)
    if pair is None:
        return q['line'], None, raw_sha, gap
    _, m = quote_only_matrices(pair['h2h']['decimal_odds'], dict(line=q['line'], **pair['total']['decimal_odds']))
    picks = score_picks(m, line=q['line'])
    return q['line'], dict(primary=picks['primary'], top3=top_three_scores(m), wdl=picks['wdl'],
                           total=picks['total']['direction']), raw_sha, None


def _signed_choices(pred, artifact, ref, f, signed_at, line, role):
    m = _matrix(pred['matrix'])
    core = artifact['core']
    selector = artifact.get('selector','joint_map')
    primary = '-'.join(map(str, select_score(m, selector)))
    _require(pred['input'] == f and pred['matrix_hash'] == digest(pred['matrix'])
             and pred['artifact_sha256'] == ref['sha256']
             and pred['version'] == ref['version'] == artifact['version']
             and core['cohort'] == f['cohort'] and pred['training_data_cutoff'] == core['train_end'] < f['date']
             and aware(artifact['created_at']) <= aware(f['cutoff_at']) <= aware(pred['generated_at']) <= aware(signed_at)
             and pred['primary'] == primary and pred.get('selector', 'joint_map') == selector
             and artifact.get('role', role) == role
             and np.isclose(pred['primary_probability'], m[tuple(map(int, primary.split('-')))], rtol=0, atol=1e-12),
             'native matrix, original primary or artifact: ' + f['match_id'])
    choices = score_picks(m, line=line)
    _require(all(np.isclose(p, pred['wdl'][WDL[d]], rtol=0, atol=1e-12)
                 for d,p in choices['wdl_probabilities'].items()) and pred['wdl_reference'] == WDL[choices['wdl']],
             'native WDL marginal')
    direction = None
    if line is not None:
        signed = pred['total']
        _require(signed and signed['quote'] == f['total_quote'], 'native total record')
        settlements = choices['total']['settlements']
        _require(all(np.isclose(p,signed[d][state],rtol=0,atol=1e-12) for d,bins in settlements.items()
                     for state,p in bins.items()), 'native total settlements')
        def balance(d):
            b = settlements[d]
            return b['win'] + .5*b['half_win'] - .5*b['half_loss'] - b['loss']
        direction = max(('over','under'), key=balance)
        _require(signed['reference_direction'] == direction, 'native total direction')
    return dict(primary=primary, top3=top_three_scores(m), wdl=choices['wdl'], total=direction)


def _bsd_rows(arc, artifact, control, f):
    core = artifact['core']
    arm, coverage = core['blend'], core['coverage']
    _require(arm['weight'] > 0 and arm['field'] in ('shotmap','expected') and
             control['core']['blend']['id'] == 'goals' and control['core']['coverage']['xg_used_sides'] == 0
             and core['history_hash'] == control['core']['history_hash']
             and core['train_rows'] == control['core']['train_rows'] and artifact['inputs'] == control['inputs'],
             'incremental xG and matched goals control')
    inputs = artifact['inputs']
    records = arc.value(inputs['xg']['path'], inputs['xg']['sha256'])
    canonical = arc.value(inputs['history']['path'], inputs['history']['sha256'])
    # Only identity, start and cohort are projected from old training history.
    history = {r['match_id']: (r['home'],r['away'],r['cohort'],aware(r['kickoff_at'])) for r in canonical}
    captures = arc.value('data/captures.json')['requests']
    receipts = {r['response_sha256']: r for r in captures if r.get('body_saved') and r['http_status'] == 200}
    ids = set()
    for row in records['rows']:
        values = row['values'].get(arm['field'])
        if row['cohort'] != f['cohort'] or not values or all(x is None for x in values):
            continue
        # These native A/B artifacts used full pairs only; the goals fallback
        # remains part of both original fitted artifacts.
        _require(len(values) == 2 and all(v is not None for v in values) and
                 row['match_id'] not in ids and row['match_id'] != f['match_id'] and
                 history.get(row['match_id']) == (row['home'],row['away'],row['cohort'],aware(row['kickoff_at'])) and
                 aware(row['kickoff_at']).date().isoformat() <= core['train_end'] < f['date'] and
                 row['quality']['status'] == 'STATS_CAPTURED' and
                 aware(row['observed_at']) <= aware(records['generated_at']) <= aware(artifact['created_at']),
                 'BSD coverage identity or origin')
        stats = arc.value(row['source_path'], row['source_sha256'])
        directory = arc.value(row['directory_source_path'], row['directory_sha256'])
        lookup = [e for e in directory['results'] if e['id'] == row['bsd_event_id']]
        _require(len(lookup) == 1 and stats['event_id'] == row['bsd_event_id']
                 and lookup[0]['league_id'] == 64 and lookup[0]['stage'] == 'group-stage'
                 and re.fullmatch(r'League ' + f['league_level'] + r', Group [1-4]', lookup[0]['group_name'])
                 and aware(lookup[0]['event_date']) == aware(row['kickoff_at'])
                 and all(_norm(lookup[0][s+'_team']) == _norm(row[s+'_name']) for s in ('home','away')),
                 'BSD directory and process identity')
        for h in (row['source_sha256'],row['directory_sha256']):
            rc = receipts[h]
            _require(rc['provider'] == 'BSD' and urlsplit(rc['url']).hostname == 'sports.bzzoiro.com'
                     and aware(rc['started_at']) <= aware(rc['ended_at']) <= aware(records['generated_at']),
                     'BSD input capture or clock')
        _require(receipts[row['source_sha256']]['url'] == row['source_url'] and
                 aware(receipts[row['source_sha256']]['ended_at']) == aware(row['observed_at']), 'BSD source receipt')
        if arm['field'] == 'shotmap':
            raw = [sum(s['xg'] for s in stats['shotmap'] if s['home'] is side and s.get('xg') is not None)
                   for side in (True,False)]
        else:
            raw = [stats['stats'][s]['expected_goals'] for s in ('home','away')]
        _require(np.allclose(values,raw,rtol=0,atol=5e-5), 'BSD original field differs')
        ids.add(row['match_id'])
    _require(len(ids) > 0 and len(ids) == coverage['xg_both_sides_rows']
             and len(ids)*2 == coverage['xg_used_sides'], 'used BSD coverage')
    return len(ids)


def _load_panel(repo, roster, panel, spec):
    arc = NativeArchive(repo, panel, spec)
    try:
        plan = arc.issued('plan.json')
        pp = plan['payload']
        _require(plan['record_sha256'] == spec['plan_record_sha256'] and pp['first_version_only'] and
                 plan['track'] == 'research-preregistration' and plan['revision'] == 1, 'native first plan')
        if panel == 'repair':
            index = arc.value(pp['index_path'], pp['index_sha256'])
            _require(pp['routes'] == index['routes'], 'repair registered routes')
        else:
            index = arc.value('experiment/index.json')
            _require(pp['index'] == index and index['selected'] == pp['selected_on_october'], 'xG original selection')
        data, used_by_tier = {}, {}
        for f in pp['fixtures_identity']:
            mid, tier = f['match_id'], f['league_level']
            _require(mid in roster and mid not in data, 'native population roster')
            r = arc.issued('freezes/' + mid.replace(':','-') + '.json')
            _validate_fixture(arc,r,f,roster[mid]['f'],plan)
            line, market, market_sha, gap = _native_market(arc,f,roster[mid])
            arms = {'team':'fixed_test','repair':'challenger'} if panel == 'repair' else {
                'team':'goals','xg':pp['selected_on_october'][tier]}
            choices, artifacts, xg_used = {}, {}, None
            for family,arm in arms.items():
                pred = r['payload']['predictions'][arm]
                ref = index['routes'][tier][arm]
                art = arc.value(ref['path'], ref['sha256'])
                _require(index['protocol_sha256'] == art['protocol_sha256'], 'native artifact protocol')
                artifacts[family] = art
                choices[family] = _signed_choices(pred, art, ref, f, r['recorded_at'], line, arm)
            if panel == 'xg':
                for family,arm in arms.items():
                    art = artifacts[family]
                    _require(art['core']['blend']['id'] == arm and art['core']['coverage'] ==
                             r['payload']['predictions'][arm]['training_coverage'], 'original xG inputs and coverage')
                if tier not in used_by_tier:
                    used_by_tier[tier] = _bsd_rows(arc,artifacts['xg'],artifacts['team'],f)
                xg_used = used_by_tier[tier]
            if market is not None:
                choices['market'] = market
            data[mid] = dict(f=f, cutoff=f['cutoff_at'], recorded_at=r['recorded_at'],
                             committed_at=arc.committed_at, record_sha=r['record_sha256'], choices=choices,
                             line=line, market_sha=market_sha, market_gap=gap, xg_used_rows=xg_used)
        _require(len(data) == (18 if panel == 'repair' else 8), 'native registered denominator')
        return data
    finally:
        arc.tar.close()


def load_native_panels(repo, roster):
    specs = json.loads((HERE/'complement-protocol.json').read_bytes())['sources']
    return {k:_load_panel(repo,roster,k,specs[k]) for k in SOURCES}
