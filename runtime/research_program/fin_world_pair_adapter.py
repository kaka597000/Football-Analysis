"""Source-verified Finland/World Cup pairs, isolated from the active registry.

Current is a fitted constant-goal reference, Test a fixed dynamic-strength
Poisson. Both are research baselines, not renamed activity models or champions.
Historical evaluation is result-date reconstruction, never certified as-of data.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np
from research_program import challengers
from v2_test.evaluation import evaluate
import active_route_engines
from active_route_engines import _normalized, _prematch_clock
from nations_league import safe_cli as label_contract
from competition_scope import require, permitted

MIN_TRAIN = 120
MIN_HOLDOUT = 20
SCHEMA = 'WCPP-FIN-WORLD-RESEARCH-PAIR-1'
INDEX_SCHEMA = 'WCPP-FIN-WORLD-PAIR-INDEX-1'
COMPETITIONS = {'fin.1', 'fifa.world'}
# Native labels only. A missing phase is never inferred from fixture date.
FIN_PHASES = {'regular season': 'regular', 'championship round': 'championship-round',
              'relegation round': 'relegation-round'}
WORLD_PHASES = {'group stage': 'group-stage', 'round of 32': 'round-of-32',
                'round of 16': 'round-of-16', 'quarterfinals': 'quarterfinals',
                'semifinals': 'semifinals', 'final': 'final',
                'third place': '3rd-place-match', '3rd place': '3rd-place-match',
                '3rd place match': '3rd-place-match'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def aware(s):
    t = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('timezone required')
    return t.astimezone(timezone.utc)


def digest(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def native_stage(header, competition):
    name = header.get('season', {}).get('name', '').lower().replace('-', ' ')
    labels = WORLD_PHASES if competition == 'fifa.world' else FIN_PHASES
    # Exact suffix is required; "semifinals" must not match "final".
    matches = [stage for label, stage in labels.items()
               if name == label or name.endswith(', ' + label)]
    if len(set(matches)) != 1:
        raise ValueError('unambiguous native competition phase required')
    return matches[0]


def source_row(meta, *, now=None):
    """Normalize one hash-verified official ESPN summary; no remote calls."""
    now = now or datetime.now(timezone.utc)
    raw_path = Path(meta['raw_path'])
    if sha(raw_path) != meta['sha256']:
        raise ValueError('raw source checksum mismatch')
    source = urlparse(meta['url'])
    competition = meta['competition']
    if (competition not in COMPETITIONS or source.scheme != 'https'
            or source.hostname != 'site.api.espn.com'
            or source.path != f'/apis/site/v2/sports/soccer/{competition}/summary'
            or set(parse_qs(source.query)) != {'event'}):
        raise ValueError('exact approved competition summary source required')
    observed = meta.get('captured_at', meta.get('query_time_utc'))
    if not observed or aware(observed) > now:
        raise ValueError('actual nonfuture source capture required')
    raw = json.loads(raw_path.read_text(encoding='utf-8'))
    h = raw['header']; games = h['competitions']
    eid = parse_qs(source.query)['event']
    if (len(eid) != 1 or not eid[0].isdigit() or str(h['id']) != eid[0]
            or h.get('league', {}).get('slug') != competition or len(games) != 1):
        raise ValueError('source event/competition identity mismatch')
    c = games[0]; status = c.get('status', {}).get('type', {})
    if (str(c['id']) != eid[0] or status.get('name') != 'STATUS_FULL_TIME'
            or status.get('completed') is not True
            or raw.get('format', {}).get('regulation', {}).get('periods') != 2
            or type(c.get('neutralSite')) is not bool):
        raise ValueError('verified full-time regulation and venue required')
    kickoff = aware(c['date'])
    if kickoff >= aware(observed) or kickoff >= now:
        raise ValueError('future result rejected')
    stage = native_stage(h, competition)
    if meta.get('stage', meta.get('phase', stage)) != stage:
        raise ValueError('declared/native phase conflict')
    sides = {v['homeAway']: v for v in c['competitors']}
    if len(c['competitors']) != 2 or set(sides) != {'home', 'away'}:
        raise ValueError('unique sides required')
    ids = []; scores = []
    for side in ('home', 'away'):
        team = sides[side]; tid = str(team['team']['id'])
        if not tid.isdigit() or len(team.get('linescores', [])) != 2:
            raise ValueError('verified team IDs and two half scores required')
        halves = []
        for v in team['linescores']:
            value = v.get('value', v.get('displayValue'))
            if isinstance(value, bool):
                raise ValueError('invalid half score')
            n = float(value)
            if not np.isfinite(n) or not n.is_integer() or not 0 <= n <= 30:
                raise ValueError('invalid half score')
            halves.append(int(n))
        total = sum(halves)
        if str(team.get('score')) != str(total):
            raise ValueError('final and regulation score conflict')
        scores.append(total); ids.append('espn:' + tid)
    if ids[0] == ids[1]:
        raise ValueError('same team on both sides')
    year = h.get('season', {}).get('year')
    if type(year) is not int or year != kickoff.year:
        raise ValueError('explicit calendar season identity required')
    return {'match_id': 'espn:' + eid[0], 'cohort': competition, 'competition': competition,
            'stage': stage, 'season': str(year), 'date': kickoff.date().isoformat(),
            'kickoff_at': kickoff.isoformat(), 'home': ids[0], 'away': ids[1],
            'home_goals': scores[0], 'away_goals': scores[1], 'neutral': c['neutralSite'],
            'score_basis': 'regulation_90', 'gender': 'men', 'age': 'senior',
            'observed_at': observed, 'first_known_at': None,
            'evidence': {'summary_url': meta['url'], 'summary_sha256': meta['sha256'],
                         'status': 'STATUS_FULL_TIME', 'regulation_periods': 2,
                         'native_phase': h['season']['name'], 'raw_path': str(raw_path.resolve()),
                         'captured_at': observed}}


def engine_identity():
    return {'adapter': sha(__file__), 'dynamic_engine': sha(challengers.__file__),
            'route_contract': sha(active_route_engines.__file__),
            'target_label_contract': sha(label_contract.__file__)}


def _reference(rows):
    nonneutral = [r for r in rows if not r['neutral']]
    neutral_rate = float(np.mean([r[k] for r in rows for k in ('home_goals', 'away_goals')]))
    means = ([float(np.mean([r[k] for r in nonneutral])) for k in ('home_goals', 'away_goals')]
             if nonneutral else [neutral_rate, neutral_rate])
    return {'home_away_rates': list(np.clip(means, .05, 8)),
            'neutral_rate': float(np.clip(neutral_rate, .05, 8))}


def _matrix(reference, dynamic, row, role):
    means = (challengers.rates(dynamic, row) if role == 'test' else
             [reference['neutral_rate']] * 2 if row['neutral'] else reference['home_away_rates'])
    return challengers.distribution(means, max_goals=80)[0]


def train_pair(rows, out, *, now=None):
    """Fixed recipes, date-block holdout, then independent final research refit.

    Callers must obtain rows through source_row; source raw metadata is retained.
    A pair is not emitted if there is insufficient verified temporal evidence.
    """
    now = now or datetime.now(timezone.utc)
    source_identity = engine_identity()
    for row in rows:
        e = row['evidence']
        verified = source_row({'competition': row['competition'], 'stage': row['stage'],
            'url': e['summary_url'], 'sha256': e['summary_sha256'],
            'raw_path': e['raw_path'], 'captured_at': e['captured_at']}, now=now)
        if verified != row:
            raise ValueError('normalized row differs from retained source')
    rows = challengers.prepare(rows)
    physical = [(r['date'], r['home'], r['away']) for r in rows]
    if len(physical) != len(set(physical)):
        raise ValueError('duplicate physical fixture')
    if any(r['cohort'] not in COMPETITIONS or not r.get('evidence', {}).get('native_phase')
           or not r.get('observed_at') or aware(r['observed_at']) > now
           or r['date'] >= now.date().isoformat() for r in rows):
        raise ValueError('verified available source rows only')
    if len(rows) < MIN_TRAIN + MIN_HOLDOUT:
        raise ValueError('insufficient phase-isolated history: 120 fit plus 20 holdout required')
    days = sorted({r['date'] for r in rows})
    eligible = [d for d in days if sum(r['date'] < d for r in rows) >= MIN_TRAIN
                and sum(r['date'] >= d for r in rows) >= MIN_HOLDOUT]
    if not eligible:
        raise ValueError('insufficient date-separated training and holdout')
    split = eligible[-1]
    prior = [r for r in rows if r['date'] < split]; holdout = [r for r in rows if r['date'] >= split]
    ref = _reference(prior); dyn = challengers.fit(prior)
    metrics = {role: evaluate([{'matrix': _matrix(ref, dyn, r, role),
                               'home_goals': r['home_goals'], 'away_goals': r['away_goals']}
                              for r in holdout]) for role in ('current', 'test')}
    artifact = {'schema': SCHEMA, 'competition': rows[0]['cohort'], 'stage': rows[0]['stage'],
                'status': 'RESEARCH_PAIR_NOT_PROMOTED',
                'engine_sha256': source_identity,
                'roles': {'current': 'FITTED_CONSTANT_GOALS_REFERENCE',
                          'test': 'FIXED_DYNAMIC_STRENGTH_POISSON'},
                'current_recipe': 'FITTED_CONSTANT_GOALS_REFERENCE',
                'test_recipe': 'FIXED_DYNAMIC_STRENGTH_POISSON',
                'reference': _reference(rows), 'dynamic': challengers.fit(rows),
                'teams': sorted({r[k] for r in rows for k in ('home', 'away')}),
                'training_n': len(rows), 'training_end': rows[-1]['date'],
                'training_rows_sha256': digest(rows), 'source_observed_max': max((r['observed_at'] for r in rows), key=aware),
                'evaluation': {'fit_n': len(prior), 'holdout_n': len(holdout), 'holdout_start': split,
                               'metrics': metrics, 'historical_asof_certified': False,
                               'evaluation_kind': 'RESULT_DATE_RECONSTRUCTION',
                               'promotion_decision': 'NOT_EVALUATED_NO_CHAMPION_CHANGE'},
                'source_evidence': [r['evidence'] for r in rows],
                'active_model_modified': False, 'automatic_promotion': False}
    if engine_identity() != source_identity:
        raise ValueError('model source changed during fitting')
    artifact['created_at'] = datetime.now(timezone.utc).isoformat()
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('x', encoding='utf-8') as f:
        json.dump(artifact, f, ensure_ascii=False, indent=2, allow_nan=False)
    return {'path': str(out.resolve()), 'sha256': sha(out), 'stage': artifact['stage'],
            'status': artifact['status'], 'training_n': len(rows)}


def predict_pair(fixture, route):
    """Read a separately registered evidence-bound pair, never active-model.json."""
    require(fixture)
    normalize = lambda key: str(key).lower().replace('-', '').replace('_', '')
    forbidden = {normalize(k) for k in label_contract.TARGET_LABELS}
    forbidden.update(normalize(k) for k in ('provider_final_score', 'result_status'))
    if any(normalize(key) in forbidden for key in fixture):
        raise ValueError('target result labels forbidden in prediction input')
    source_identity = engine_identity()
    index_path = Path(route['index_path'])
    if sha(index_path) != route['index_sha256']:
        raise ValueError('research index checksum mismatch')
    index = json.loads(index_path.read_text(encoding='utf-8'))
    comp = fixture.get('competition', fixture.get('cohort'))
    if (index.get('schema') != INDEX_SCHEMA or comp not in COMPETITIONS
            or fixture.get('cohort') != comp or index.get('competition') != comp
            or route.get('status') != 'READY_RESEARCH_PAIR'):
        raise ValueError('approved research competition route required')
    stage = fixture.get('stage'); entry = index['stages'].get(stage)
    if entry is None:
        raise ValueError('phase has no verified dual pair')
    p = Path(entry['path'])
    if not p.is_absolute(): p = index_path.parent / p
    if not p.resolve().is_relative_to(index_path.parent.resolve()) or sha(p) != entry['sha256']:
        raise ValueError('trusted pair artifact checksum required')
    a = json.loads(p.read_text(encoding='utf-8'))
    if (a.get('schema') != SCHEMA or a['competition'] != comp or a['stage'] != stage
            or a.get('engine_sha256') != engine_identity()
            or a.get('roles') != {'current': 'FITTED_CONSTANT_GOALS_REFERENCE',
                                  'test': 'FIXED_DYNAMIC_STRENGTH_POISSON'}
            or a.get('dynamic', {}).get('schema') != 'WCPP-DYNAMIC-STRENGTH-1'
            or a['dynamic'].get('cohort') != comp or a['dynamic'].get('stage') != stage
            or a['dynamic'].get('training_n') != a['training_n']
            or a['dynamic'].get('training_end') != a['training_end']
            or a.get('automatic_promotion') is not False or a.get('active_model_modified') is not False
            or a['training_n'] < MIN_TRAIN + MIN_HOLDOUT
            or a['evaluation']['fit_n'] < MIN_TRAIN or a['evaluation']['holdout_n'] < MIN_HOLDOUT):
        raise ValueError('verified research artifact and temporal validation required')
    _prematch_clock(fixture, built_at=a['created_at'])
    _prematch_clock(fixture, built_at=index['created_at'])
    if (fixture.get('score_basis') != 'regulation_90' or fixture.get('gender', 'men') != 'men'
            or fixture.get('age', 'senior') != 'senior' or type(fixture.get('neutral')) is not bool
            or fixture['date'] <= a['training_end']
            or aware(fixture['kickoff_at']).date().isoformat() != fixture['date']
            or any(fixture[k] not in a['teams'] for k in ('home', 'away'))
            or fixture['home'] == fixture['away']):
        raise ValueError('verified adult-men identity, venue and post-training target required')
    result = {}
    for role in ('current', 'test'):
        m = _matrix(a['reference'], a['dynamic'], fixture, role)
        result[role + '_model'] = _normalized({'matrix': m.tolist(), 'status': 'RESEARCH_ONLY'}, fixture,
            version=SCHEMA + ':' + role, role=role, artifact_path=p,
            training_cutoff=a['training_end'])
    if (engine_identity() != source_identity or sha(index_path) != route['index_sha256']
            or sha(p) != entry['sha256']):
        raise ValueError('source, index or artifact changed during inference')
    return dict(result, research_only=True, formal_promotion=False,
                execution_audit={'index_sha256': sha(index_path), 'artifact_sha256': sha(p),
                                 'training_rows_sha256': a['training_rows_sha256'],
                                 'engine_sha256': engine_identity(), 'recipe_current': a['current_recipe'], 'recipe_test': a['test_recipe'],
                                 'historical_asof_certified': False})


def build_index(manifest, out, competition):
    """Build only genuinely fitted stages; retain all rejected/blocked counts."""
    require({'cohort': competition, 'competition': competition})
    if competition not in COMPETITIONS:
        raise ValueError('unsupported research competition')
    out = Path(out); out.mkdir(parents=True, exist_ok=False)
    rows = []; rejected = []
    for entry in manifest:
        if entry.get('competition') != competition:
            raise ValueError('one declared competition per manifest')
        try:
            rows.append(source_row(entry))
        except (ValueError, TypeError, KeyError, OSError, IndexError) as exc:
            rejected.append({'source_sha256': entry.get('sha256'), 'error_type': type(exc).__name__,
                             'reason': str(exc) if isinstance(exc, ValueError) else 'invalid or unavailable retained source'})
    stages = {}; blocked = {}
    for stage in sorted({r['stage'] for r in rows}):
        group = [r for r in rows if r['stage'] == stage]
        try:
            result = train_pair(group, out/(stage + '-pair.json'))
            result['path'] = Path(result['path']).name
            stages[stage] = result
        except ValueError as exc:
            blocked[stage] = {'status': 'BLOCKED', 'verified_rows': len(group), 'reason': str(exc)}
    index = {'schema': INDEX_SCHEMA, 'competition': competition, 'stages': stages,
             'blocked_stages': blocked, 'rejected_sources': rejected,
             'verified_rows': len(rows), 'source_manifest_sha256': digest(manifest),
             'created_at': datetime.now(timezone.utc).isoformat(),
             'status': 'PARTIAL_RESEARCH_READY' if stages else 'BLOCKED_NO_VERIFIED_PAIR',
             'coverage_rule': 'ONLY_EXPLICIT_READY_STAGES; NO_FULL_COMPETITION_CLAIM',
             'automatic_promotion': False, 'active_model_modified': False}
    with (out/'index.json').open('x', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2, allow_nan=False)
    with (out/'verified-history.json').open('x', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2, allow_nan=False)
    return index


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, help='List of local retained-source metadata records')
    parser.add_argument('--out', required=True, help='New directory only')
    parser.add_argument('--competition', choices=sorted(c for c in COMPETITIONS if permitted({'cohort': c})), required=True)
    args = parser.parse_args()
    value = build_index(json.loads(Path(args.manifest).read_text(encoding='utf-8')), args.out, args.competition)
    print(json.dumps({k: value[k] for k in ('competition', 'status', 'verified_rows', 'blocked_stages')}))
