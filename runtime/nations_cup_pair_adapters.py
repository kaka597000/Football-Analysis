"""Opt-in, hash-checked research pairs. No registry mutation or publication.

A relocation map names exact original files, never rewrites trained artifacts.
The existing strict source validators and immutable numerical cores are reused.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from datetime import datetime, timezone

import numpy as np


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Resolver:
    def __init__(self, paths=None):
        self.paths = paths or {}
        self.checked = {}

    def file(self, original, expected):
        original = str(original)
        actual = str(self.paths.get(original, original))
        if not isinstance(expected, str) or len(expected) != 64 or sha(actual) != expected:
            raise ValueError('research dependency checksum: ' + original)
        if actual != original and Path(original).exists() and sha(original) != expected:
            raise ValueError('conflicting original dependency: ' + original)
        self.checked[original] = {'path': actual, 'sha256': expected}
        return actual

    def read(self, original, expected):
        return json.loads(Path(self.file(original, expected)).read_text(encoding='utf-8'))

    def recheck(self):
        for original, ref in list(self.checked.items()):
            self.file(original, ref['sha256'])


def _source_modules(expected, modules):
    """Relocation is allowed only for the exact known module set and bytes."""
    actual = {name: sha(module.__file__) for name, module in modules.items()}
    normalized = {}
    for path, checksum in expected.items():
        matches = [name for name in actual if str(path).endswith('/' + name)]
        if len(matches) != 1 or matches[0] in normalized:
            raise ValueError('unknown/duplicate executed source dependency')
        normalized[matches[0]] = checksum
    if normalized != actual:
        raise ValueError('executed research sources differ from trained sources')
    return actual


def _nations(index, fixture, resolver):
    from nations_league import repair, safe_cli, database, model
    from research_program import nations_phase
    from v2 import model as numerical
    safe_cli.validate_future(fixture)
    if index.get('schema') != 'WCPP-NATIONS-REPAIR-INDEX-1':
        raise ValueError('Nations repair index required')
    resolver.file(index['protocol_path'], index['protocol_sha256'])
    pair = {}
    sources = {}
    for role in ('current', 'test'):
        ref = index['routes'][fixture['league_level']]['fixed_' + role]
        a = resolver.read(ref['path'], ref['sha256'])
        if (a.get('schema') != repair.SCHEMA or a.get('role') != 'fixed_' + role
                or a.get('numeric_role') != role or a['cohort'] != fixture['cohort']
                or a['league_level'] != fixture['league_level'] or a['stage'] != fixture['stage']):
            raise ValueError('exact Nations role/level/stage required')
        sources = _source_modules(a['dependencies'], {
            'nations_league/repair.py': repair, 'nations_league/model.py': model,
            'nations_league/database.py': database, 'nations_league/safe_cli.py': safe_cli,
            'research_program/nations_phase.py': nations_phase, 'v2/model.py': numerical})
        for kind in ('dataset', 'protocol'):
            resolver.file(a[kind + '_path'], a[kind + '_sha256'])
        if (a['train_end'] != a['core']['train_end'] or a['train_end'] >= fixture['date']
                or model.aware(a['created_at']) > model.aware(fixture['cutoff_at'])):
            raise ValueError('Nations training/availability boundary')
        m, mu, missing = repair.distribution(a['core'], a['recipe'], fixture,
                                             a['numeric_role'], a['temperature'])
        pair[role] = _output(m, a, fixture, ref['sha256'], repair.select_score(m, a['selector']))
        pair[role].update(base_rates=mu, unseen_teams_using_fitted_global_prior=missing)
    safe_cli.validate_future(fixture)
    if _source_modules(a['dependencies'], {
            'nations_league/repair.py': repair, 'nations_league/model.py': model,
            'nations_league/database.py': database, 'nations_league/safe_cli.py': safe_cli,
            'research_program/nations_phase.py': nations_phase, 'v2/model.py': numerical}) != sources:
        raise ValueError('Nations executed source changed during pair')
    return pair, sources


def _mapped_sources(value, resolver):
    value = copy.deepcopy(value)
    def visit(obj):
        if isinstance(obj, dict):
            for path_key, hash_key in (('raw_path', 'raw_sha256'), ('source_path', 'source_sha256')):
                if path_key in obj and hash_key in obj:
                    obj[path_key] = resolver.file(obj[path_key], obj[hash_key])
            for child in obj.values():
                visit(child)
        elif isinstance(obj, list):
            for child in obj:
                visit(child)
    visit(value)
    return value


def _cup(index, fixture, resolver):
    from jleague_cup import quarterfinals as q, model, evidence, database, safe_evidence
    safe_evidence.validate_target(fixture)
    if index.get('schema') != q.SCHEMA or type(fixture.get('leg')) is not int or fixture['leg'] not in (1, 2):
        raise ValueError('Cup quarterfinal index and exact leg required')
    if fixture.get('cohort') != 'jpn.league_cup:qf-transfer:leg' + str(fixture['leg']):
        raise ValueError('exact Cup research cohort required')
    refs = index['routes'][str(fixture['leg'])]['artifacts']
    if set(refs) != {'current', 'test'}:
        raise ValueError('independent Cup pair required')
    pair = {}; sources = {}
    for role in ('current', 'test'):
        ref = refs[role]; a = resolver.read(ref['path'], ref['sha256'])
        if a.get('schema') != q.SCHEMA or a.get('role') != role or a['leg'] != fixture['leg']:
            raise ValueError('Cup artifact role/leg mismatch')
        deps = a['dependencies']
        engines = {'engine': q, 'core_engine': model, 'evidence_engine': evidence, 'database_engine': database}
        if not {'database', 'certificate', 'protocol', 'dataset', *engines} <= deps.keys():
            raise ValueError('complete Cup dependencies required')
        for key, dep in deps.items():
            resolver.file(dep['path'], dep['sha256'])
        for key, module in engines.items():
            if sha(module.__file__) != deps[key]['sha256']:
                raise ValueError('executed Cup core mismatch')
            sources[key] = sha(module.__file__)
        certificate = resolver.read(deps['certificate']['path'], deps['certificate']['sha256'])
        # Only transient certificate paths change; original certificate remains hash-bound.
        with tempfile.TemporaryDirectory(prefix='cup-certificate-') as folder:
            path = Path(folder) / 'certificate.json'
            path.write_text(json.dumps(_mapped_sources(certificate, resolver)), encoding='utf-8')
            cert = q.certificate(path)
        q.target_identity(fixture, cert)
        if (a['core']['fit_end'] >= fixture['date']
                or evidence.aware(a['built_at']) > evidence.aware(fixture['cutoff_at'])):
            raise ValueError('Cup training/availability boundary')
        aggregate = None
        if fixture['leg'] == 2:
            first = fixture.get('first_leg_record')
            if not first:
                raise ValueError('SECOND_LEG_REQUIRES_VERIFIED_FIRST_LEG_RESULT')
            fact = safe_evidence.verify_row(first)
            if (fact['stage'] != 'quarterfinals' or fact['leg'] != 1 or fact['season'] != '2026'
                    or fact['date'] >= fixture['date']
                    or database.team(fact['home_name']) != fixture['away']
                    or database.team(fact['away_name']) != fixture['home']):
                raise ValueError('first-leg identity mismatch')
            for key in ('evidence', 'period_evidence'):
                s = first.get(key)
                if s and (not s.get('query_time') or evidence.aware(s['query_time']) > evidence.aware(fixture['cutoff_at'])):
                    raise ValueError('first-leg source not known at cutoff')
            aggregate = {'home': fact['away_90'], 'away': fact['home_90']}
        m, mu = model.distribution(a['core'], fixture)
        pair[role] = _output(m, a, fixture, ref['sha256'], np.unravel_index(m.argmax(), m.shape))
        pair[role].update(aggregate_before=aggregate, aggregate_context_trained=False, base_rates=mu)
    q.target_identity(fixture, cert)
    if any(sha(module.__file__) != sources[key] for key, module in engines.items()):
        raise ValueError('Cup executed source changed during pair')
    sources.update(validator=sha(safe_evidence.__file__))
    return pair, sources


def _output(matrix, artifact, fixture, checksum, selected):
    from research.analysis_freeze import digest
    m = np.asarray(matrix); h, a = np.indices(m.shape)
    if m.ndim != 2 or not np.isfinite(m).all() or (m < 0).any() or abs(m.sum() - 1) > 1e-8:
        raise ValueError('valid full probability matrix required')
    wdl = dict(zip(('home', 'draw', 'away'), map(float, (m[h > a].sum(), m[h == a].sum(), m[h < a].sum()))))
    return {'version': artifact['version'], 'role': artifact['role'], 'input': copy.deepcopy(fixture),
            'status': 'INACTIVE_RESEARCH_CANDIDATE', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'expected_goals': [float((h*m).sum()), float((a*m).sum())],
            'primary_probability': float(m[tuple(selected)]), 'matrix': m.tolist(), 'matrix_hash': digest(m.tolist()),
            'primary': '-'.join(str(int(x)) for x in selected),
            'statistical_top1': '-'.join(str(int(x)) for x in np.unravel_index(m.argmax(), m.shape)),
            'primary_scope': 'STATISTICAL_REFERENCE_NOT_COMPREHENSIVE_RECOMMENDATION',
            'wdl': wdl, 'wdl_reference': max(wdl, key=wdl.get), 'artifact_sha256': checksum,
            'training_data_cutoff': artifact.get('train_end', artifact['core'].get('fit_end')),
            'active_route': False, 'formal_kpi_eligible': False, 'automatic_promotion': False}


def predict_pair(fixture, route):
    """Compute both roles or raise; caller must separately freeze before publication."""
    if route.get('engine') != 'nations_cup' or route.get('status') != 'READY_RESEARCH_PAIR':
        raise ValueError('explicit ready research route required')
    competition = fixture.get('competition')
    if route.get('competition') != competition or fixture.get('stage') not in route.get('stages', []):
        raise ValueError('exact research competition/stage required')
    adapter_hash = sha(__file__)
    resolver = Resolver(route.get('dependency_paths'))
    index = resolver.read(route['index_path'], route['index_sha256'])
    fixture = _mapped_sources(fixture, resolver)
    if competition == 'uefa.nations':
        pair, sources = _nations(index, fixture, resolver)
    elif competition == 'jpn.league_cup':
        pair, sources = _cup(index, fixture, resolver)
    else:
        raise ValueError('unsupported national/Cup research competition')
    resolver.recheck()
    if sha(__file__) != adapter_hash:
        raise ValueError('adapter changed during pair')
    return {'match_id': fixture['match_id'], 'competition': competition,
            'cohort': fixture['cohort'], 'stage': fixture['stage'],
            'current_model': pair['current'], 'test_model': pair['test'],
            'status': 'RESEARCH_PAIR_NOT_CHAMPION', 'active_route': False,
            'formal_kpi_eligible': False, 'automatic_promotion': False,
            'publication_required': True, 'selection_policy': 'INDEPENDENT_PAIR_NO_BEST_OF_TWO',
            'execution_audit': {'adapter_sha256': sha(__file__), 'sources': sources,
                                'dependencies': resolver.checked, 'index_sha256': route['index_sha256']}}
