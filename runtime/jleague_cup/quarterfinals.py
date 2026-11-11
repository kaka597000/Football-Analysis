"""Mechanism-aligned QF transfer research; old edition artifacts stay immutable."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
from urllib.parse import urlparse

import numpy as np

from . import database, evidence, model

RULES = {'quarterfinal_legs': 2, 'regulation_minutes': 90,
         'away_goals_rule': False, 'first_leg_tie_resolution': 'draw_stands',
         'second_leg_tie_resolution': 'aggregate_then_extra_time_penalties'}
SCHEMA = 'WCPP-JCUP-QF-TRANSFER-1'


def read(path):
    return json.loads(Path(path).read_text())


def text(raw):
    raw = re.sub(r'<!--.*?-->', '', raw, flags=re.S)
    raw = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', raw, flags=re.S | re.I)
    return re.sub(r'\s+', '', html.unescape(re.sub('<[^>]*>', '', raw)))


def official_source(s):
    u = urlparse(s['source_url'])
    if (u.scheme != 'https' or u.hostname not in ('www.jleague.jp', 'jleague.jp')
            or u.port not in (None, 443) or u.username or u.password):
        raise ValueError('official J.League source required')
    if evidence.sha(s['raw_path']) != s['raw_sha256']:
        raise ValueError('official source hash mismatch')
    if evidence.aware(s['query_time']) > datetime.now(timezone.utc):
        raise ValueError('future source time')
    return Path(s['raw_path']).read_text()


def certificate(path):
    c = read(path)
    if (c.get('schema') != 'WCPP-JCUP-QF-RULES-1' or c.get('reviewed') is not True
            or c.get('compatibility_scope') != 'quarterfinal_mechanics_only'
            or c.get('target_edition') != '2026/27'):
        raise ValueError('explicit reviewed mechanism-only certificate required')
    editions = c['historical_editions']
    if not editions or len(set(editions)) != len(editions):
        raise ValueError('unique historical editions required')
    for year in [*editions, c['target_edition']]:
        entry = c['editions'][year]
        if entry['rules'] != RULES:
            raise ValueError('incompatible quarterfinal rules: ' + year)
        for field in RULES:
            proofs = entry['support'].get(field)
            if not proofs:
                raise ValueError('missing rule support: ' + field)
            for proof in proofs:
                raw = official_source(proof)
                if not proof.get('excerpt') or text(proof['excerpt']) not in text(raw):
                    raise ValueError('rule excerpt absent from retained official response')
    for code, alias in c.get('team_aliases', {}).items():
        if (alias.get('reviewed') is not True or not alias.get('historical_name')
                or not alias.get('official_name')):
            raise ValueError('reviewed team alias required')
        raw = official_source(alias['english_source'])
        teams = [m[side + 'Team'] for m in official_match_objects(raw) for side in ('home', 'away')]
        if not any(t.get('code') == code and t.get('name') == alias['official_english_name'] for t in teams):
            raise ValueError('official English team identity absent')
    return c


def pair_history(history, editions):
    """Fail closed on incomplete editions, ambiguous ties and reversed chronology."""
    result = []
    for year in editions:
        candidates = [dict(r) for r in history if str(r['season']) == year
                      and r['stage'] == 'quarterfinals']
        if len(candidates) != 8:
            raise ValueError('exact eight verified QF matches required: ' + year)
        ties = defaultdict(list)
        for r in candidates:
            if r['tie_format'] != 'two-leg' or type(r.get('leg')) is not int or r['leg'] not in (1, 2):
                raise ValueError('explicit QF leg required')
            ties[tuple(sorted((r['home'], r['away'])))].append(r)
        if len(ties) != 4:
            raise ValueError('exact four unique ties required')
        for teams, pair in sorted(ties.items()):
            if len(pair) != 2 or {r['leg'] for r in pair} != {1, 2}:
                raise ValueError('one match per leg required')
            first, second = sorted(pair, key=lambda r: r['leg'])
            if (first['date'] >= second['date'] or first['home'] != second['away']
                    or first['away'] != second['home']):
                raise ValueError('tie orientation or chronology conflict')
            if first.get('home_et') or first.get('away_et') or first.get('home_pen') or first.get('away_pen'):
                raise ValueError('first leg cannot contain ET or penalties')
            for r in pair:
                r['source_cohort'] = r['cohort']
                r['cohort'] = 'jpn.league_cup:qf-transfer:leg' + str(r['leg'])
                r['tie_id'] = year + ':' + database.key(teams)[:16]
                r['aggregate_context_trained'] = False
                r['first_leg_match_id'] = first['match_id'] if r['leg'] == 2 else None
                r['aggregate_before_home'] = first['away_goals'] if r['leg'] == 2 else 0
                r['aggregate_before_away'] = first['home_goals'] if r['leg'] == 2 else 0
                result.append(r)
    return sorted(result, key=lambda r: (r['date'], r['match_id']))


def prepare(db, rules, protocol):
    c = certificate(rules)
    p = read(protocol)
    if (p.get('schema') != 'WCPP-JCUP-QF-TRANSFER-PROTOCOL-1'
            or p.get('target_edition') != c['target_edition']
            or p.get('hyperparameter_search') is not False
            or p.get('automatic_activation') is not False
            or p.get('minimum_future_acceptance_per_leg', 0) < 30):
        raise ValueError('preregistered conservative QF protocol required')
    if not set(map(int, c['historical_editions'])) <= set(p['allowed_history_years']):
        raise ValueError('edition outside preregistration')
    if evidence.aware(p['created_at']) > datetime.now(timezone.utc):
        raise ValueError('future preregistration')
    history = pair_history(database.rows(db), c['historical_editions'])
    return history, c, p


def train(db, rules, protocol, out):
    history, cert, plan = prepare(db, rules, protocol)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    model.write(out / 'dataset.json', history)
    dependencies = {k: {'path': str(Path(p).resolve()), 'sha256': evidence.sha(p)}
                    for k, p in {'database': db, 'certificate': rules,
                                 'protocol': protocol, 'dataset': out / 'dataset.json',
                                 'engine': __file__, 'core_engine': model.__file__,
                                 'evidence_engine': evidence.__file__,
                                 'database_engine': database.__file__}.items()}
    index = {'schema': SCHEMA, 'routes': {}, 'untrained': [],
             'mechanics_only_transfer': True, 'active_registry_modified': False}
    for leg in (1, 2):
        rows = [r for r in history if r['leg'] == leg]
        if len(rows) < plan['minimum_history_per_leg']:
            index['untrained'].append({'leg': leg, 'n': len(rows), 'reason': 'INSUFFICIENT_HISTORY'})
            continue
        folder = out / ('leg' + str(leg))
        folder.mkdir()
        evaluated = {'current': [], 'test': []}
        folds = []
        for year in sorted({r['season'] for r in rows}):
            fit = [r for r in rows if r['season'] < year]
            targets = [r for r in rows if r['season'] == year]
            if len(fit) < plan['minimum_fit_per_leg']:
                continue
            if max(r['date'] for r in fit) >= min(r['date'] for r in targets):
                raise ValueError('temporal fold overlap')
            fold = {'target_edition': year, 'fit_ids': [r['match_id'] for r in fit],
                    'target_ids': [r['match_id'] for r in targets], 'fit_end': max(r['date'] for r in fit),
                    'cores': {}, 'predictions': {}}
            for role in ('current', 'test'):
                core = model.fit(fit, role)
                fold['cores'][role] = core
                fold['predictions'][role] = []
                for r in targets:
                    m, mu = model.distribution(core, r)
                    score = [int(x) for x in np.unravel_index(m.argmax(), m.shape)]
                    evaluated[role].append({'matrix': m, 'home_goals': r['home_goals'],
                                            'away_goals': r['away_goals'], 'primary_score': score})
                    fold['predictions'][role].append({'match_id': r['match_id'], 'matrix': m.tolist(),
                        'expected_goals': mu, 'statistical_top1': score,
                        'actual': [r['home_goals'], r['away_goals']],
                        'unseen_teams': [r[s] for s in ('home', 'away') if role == 'test' and r[s] not in core['teams']]})
            folds.append(fold)
        if not folds:
            index['untrained'].append({'leg': leg, 'reason': 'NO_TIME_HELD_OUT_FOLD'})
            continue
        metrics = {role: model.evaluate(values) for role, values in evaluated.items()}
        report = {'leg': leg, 'n': len(rows), 'folds': folds, 'metrics': metrics,
                  'diagnostic_type': 'RESULT_DATE_ISOLATED_NOT_ASOF_CERTIFIED',
                  'formal_acceptance_passed': False, 'market_metrics': None,
                  'blocking_reasons': ['NO_COMPLETED_PROSPECTIVE_VALIDATION', 'SMALL_SAMPLE',
                      'VENUE_UNKNOWN', 'FIRST_KNOWN_UNKNOWN', 'NO_ACTUAL_MARKET_AUDIT',
                      'POPULATION_TRANSFER_UNVALIDATED', 'AGGREGATE_CONTEXT_COEFFICIENT_NOT_TRAINED']}
        model.write(folder / 'walk-forward.json', report)
        artifacts = {}
        for role in ('current', 'test'):
            artifact = {'schema': SCHEMA, 'version': 'WCPP-JCUP-QF-' + role.upper() + '-0.1',
                        'leg': leg, 'role': role, 'core': model.fit(rows, role),
                        'built_at': datetime.now(timezone.utc).isoformat(), 'dependencies': dependencies,
                        'target_edition': cert['target_edition'], 'historical_editions': cert['historical_editions'],
                        'scope': 'MECHANISM_ALIGNED_TRANSFER_RESEARCH_ONLY',
                        'formal_acceptance_passed': False, 'active': False,
                        'trained_xg': False, 'aggregate_context_trained': False}
            path = folder / (role + '-artifact.json')
            model.write(path, artifact)
            artifacts[role] = {'path': str(path.resolve()), 'sha256': evidence.sha(path)}
        index['routes'][str(leg)] = {'artifacts': artifacts, 'n': len(rows),
                    'diagnostic_n': metrics['current']['n'], 'metrics': metrics,
                    'report_path': str((folder / 'walk-forward.json').resolve())}
    model.write(out / 'candidates.json', index)
    return index


def official_match_objects(raw):
    chunks = re.findall(r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)', raw)
    decoded = ''.join(json.loads(s) for s in chunks)
    objects = []
    for line in decoded.splitlines():
        for candidate in (line, line.partition(':')[2]):
            try:
                stack = [json.loads(candidate)]
            except (json.JSONDecodeError, ValueError):
                continue
            while stack:
                value = stack.pop()
                if isinstance(value, dict):
                    if all(k in value for k in ('homeTeam', 'awayTeam', 'date')):
                        objects.append(value)
                    stack.extend(value.values())
                elif isinstance(value, list):
                    stack.extend(value)
    return objects


def target_identity(f, cert):
    if (f.get('competition') != 'jpn.league_cup' or f.get('edition') != cert['target_edition']
            or f.get('gender') not in ('men', 'male') or f.get('age') != 'senior'
            or f.get('stage') != 'quarterfinals' or f.get('tie_format') != 'two-leg'
            or type(f.get('leg')) is not int or f['leg'] not in (1, 2)
            or f.get('phase') != 'PreMatch'):
        raise ValueError('exact senior-men target edition QF leg required')
    if any(k in f for k in ('home_goals', 'away_goals', 'actual', 'result', 'h90', 'a90',
                               'home_90', 'away_90', 'home_et', 'away_et', 'home_pen', 'away_pen')):
        raise ValueError('target labels prohibited')
    cutoff, kickoff = evidence.aware(f['cutoff_at']), evidence.aware(f['kickoff_at'])
    if not cutoff <= datetime.now(timezone.utc) < kickoff or cutoff >= kickoff:
        raise ValueError('actual future prematch required')
    if f['date'] != kickoff.date().isoformat() or f.get('neutral') is not None:
        raise ValueError('date must match; venue remains unknown without independent evidence')
    s = f['phase_evidence']
    raw = official_source(s)
    u = urlparse(s['source_url'])
    if not re.fullmatch(r'/match/leaguecup/2026/\d{6}/', u.path):
        raise ValueError('exact official event endpoint required')
    if f['match_id'] != 'jleague:' + u.path.rstrip('/').split('/')[-1]:
        raise ValueError('official event ID mismatch')
    canonical = re.search(r'<link[^>]*rel="canonical"[^>]*href="([^"]+)"', raw)
    if not canonical or html.unescape(canonical[1]) != s['source_url']:
        raise ValueError('canonical event mismatch')
    if evidence.aware(s['query_time']) > cutoff:
        raise ValueError('source unavailable at cutoff')
    if text('準々決勝 第' + str(f['leg']) + '戦') not in text(raw):
        raise ValueError('official phase/leg absent')
    objects = official_match_objects(raw)
    identities = {(o['homeTeam'].get('code'), o['awayTeam'].get('code'), o['date'])
                  for o in objects if isinstance(o['homeTeam'], dict) and isinstance(o['awayTeam'], dict)}
    if len(identities) != 1:
        raise ValueError('one unambiguous official event object required')
    match = objects[0]
    if not isinstance(match['date'], str) or not match['date'].startswith('$D'):
        raise ValueError('official event date missing')
    if evidence.aware(match['date'][2:]) != kickoff:
        raise ValueError('official kickoff mismatch')
    for side in ('home', 'away'):
        obj = match[side + 'Team']
        code = f[side + '_official_code']
        mapping = cert['team_aliases'].get(code)
        if (obj.get('code') != code or not mapping or mapping.get('reviewed') is not True
                or f[side] != database.team(mapping['historical_name'])
                or obj.get('name') != mapping['official_name']):
            raise ValueError('reviewed exact official-to-history team mapping required')
    if f['home'] == f['away']:
        raise ValueError('different teams required')


def predict(path, fixture):
    a = read(path)
    if a.get('schema') != SCHEMA:
        raise ValueError('QF transfer artifact required')
    for d in a['dependencies'].values():
        if evidence.sha(d['path']) != d['sha256']:
            raise ValueError('QF candidate dependency changed')
    cert = certificate(a['dependencies']['certificate']['path'])
    target_identity(fixture, cert)
    if (fixture['leg'] != a['leg'] or a['core']['fit_end'] >= fixture['date']
            or evidence.aware(a['built_at']) > evidence.aware(fixture['cutoff_at'])):
        raise ValueError('candidate leg/training/availability mismatch')
    aggregate = None
    if fixture['leg'] == 2:
        first = fixture.get('first_leg_record')
        if not first:
            raise ValueError('SECOND_LEG_REQUIRES_VERIFIED_FIRST_LEG_RESULT')
        fact = evidence.verify_row(first)
        if (fact['stage'] != 'quarterfinals' or fact['leg'] != 1 or fact['season'] != '2026'
                or fact['date'] >= fixture['date']
                or database.team(fact['home_name']) != fixture['away']
                or database.team(fact['away_name']) != fixture['home']):
            raise ValueError('first-leg identity mismatch')
        for k in ('evidence', 'period_evidence'):
            s = first.get(k)
            if s and (not s.get('query_time') or evidence.aware(s['query_time']) > evidence.aware(fixture['cutoff_at'])):
                raise ValueError('first-leg source not known at cutoff')
        aggregate = {'home': fact['away_90'], 'away': fact['home_90']}
    m, mu = model.distribution(a['core'], fixture)
    return {'version': a['version'], 'role': a['role'], 'status': 'EXPERIMENTAL_QF_TRANSFER_RESEARCH',
            'matrix': m.tolist(), 'matrix_sha256': database.key(m.tolist()),
            'statistical_top1': [int(x) for x in np.unravel_index(m.argmax(), m.shape)],
            'expected_goals': mu, 'training_data_cutoff': a['core']['fit_end'],
            'unseen_teams_using_fitted_prior': [fixture[s] for s in ('home', 'away')
                    if a['role'] == 'test' and fixture[s] not in a['core']['teams']],
            'aggregate_before': aggregate, 'aggregate_context_trained': False,
            'formal_acceptance_passed': False, 'formal_kpi_eligible': False,
            'comprehensive_primary_ready': False, 'actual_line': None,
            'venue_unknown': True, 'historical_asof_certified': False}



def freeze_pairs(index_path, fixtures_path, out, freeze_root=None):
    from research.analysis_freeze import save

    index = read(index_path)
    fixtures = read(fixtures_path)
    if not fixtures or len({f['match_id'] for f in fixtures}) != len(fixtures):
        raise ValueError('unique nonempty prospective universe required')
    artifacts = {leg: entry['artifacts'] for leg, entry in index['routes'].items()}
    for f in fixtures:
        pair = artifacts[str(f['leg'])]
        for role in ('current', 'test'):
            ref = pair[role]
            if evidence.sha(ref['path']) != ref['sha256']:
                raise ValueError('candidate index hash mismatch')
            a = read(ref['path'])
            target_identity(f, certificate(a['dependencies']['certificate']['path']))
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    kwargs = {'root': freeze_root} if freeze_root else {}
    scope = {'fixture_ids': [f['match_id'] for f in fixtures], 'artifacts': artifacts,
             'track': 'jleague-cup-quarterfinal-candidates', 'active_registry_modified': False,
             'selection': 'complete supplied universe; leg2 deferred until first result exists'}
    batch = {'match_id': 'jcup-qf-scope-' + database.key(scope)[:16],
             'cutoff_at': max(fixtures, key=lambda f: evidence.aware(f['cutoff_at']))['cutoff_at'],
             'kickoff_at': min(fixtures, key=lambda f: evidence.aware(f['kickoff_at']))['kickoff_at']}
    scope['freeze_receipt'] = save(scope, batch, stage='prematch', track='research-scope', **kwargs)
    model.write(out / 'scope.json', scope)
    receipts, deferred = [], []
    for f in fixtures:
        if f['leg'] == 2 and not f.get('first_leg_record'):
            deferred.append({'match_id': f['match_id'], 'reason': 'FIRST_LEG_NOT_YET_PLAYED_OR_VERIFIED',
                             'prediction_issued': False})
            continue
        pair = artifacts[str(f['leg'])]
        result = {role: predict(ref['path'], f) for role, ref in pair.items()}
        result.update(scope_freeze_reference=scope['freeze_receipt'],
                      active_registry_modified=False, comprehensive_primary_ready=False)
        receipt = save(result, f, stage='prematch', track='jleague-cup-quarterfinal-candidates', **kwargs)
        model.write(out / (database.key(f['match_id'])[:16] + '.json'),
                    {'fixture': f, 'payload': result, 'freeze_receipt': receipt})
        receipts.append(receipt)
    result = {'scope_freeze': scope['freeze_receipt'], 'receipts': receipts, 'deferred': deferred}
    model.write(out / 'receipts.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    t = sub.add_parser('train')
    for name in ('db', 'rules', 'protocol', 'out'):
        t.add_argument('--' + name, required=True)
    t = sub.add_parser('predict')
    for name in ('artifact', 'fixture', 'out'):
        t.add_argument('--' + name, required=True)
    t = sub.add_parser('freeze-pairs')
    for name in ('index', 'fixtures', 'out'):
        t.add_argument('--' + name, required=True)
    t.add_argument('--freeze-root')
    a = p.parse_args()
    if a.command == 'train':
        result = train(a.db, a.rules, a.protocol, a.out)
        print(json.dumps({'trained_legs': list(result['routes']), 'untrained': result['untrained']}))
    elif a.command == 'freeze-pairs':
        result = freeze_pairs(a.index, a.fixtures, a.out, a.freeze_root)
        print(json.dumps({'issued': len(result['receipts']), 'deferred': result['deferred']}))
    else:
        model.write(a.out, predict(a.artifact, read(a.fixture)))
        print(a.out)


if __name__ == '__main__':
    main()
