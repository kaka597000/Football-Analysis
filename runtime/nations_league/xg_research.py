"""Execute goal/xG target-blend experiments without changing retained models.

Missing xG falls back to observed goals. Provider disagreements and estimated
flags remain in the input evidence; each field is a separate experimental arm.
Calendar replay is development evidence, not historical as-of certification.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from . import repair
from .repair_forward import attach_total
from research.analysis_freeze import aware, digest, save, verify
from v2.model import rates, total_settlement, validate_history

FIELDS = ('expected', 'actual', 'shotmap')
ARMS = [{'id': 'goals', 'field': None, 'weight': 0.}] + [
    {'id': f'{field}-{int(weight*100)}', 'field': field, 'weight': weight}
    for field in FIELDS for weight in (.25, .5, .75, 1.)]


def now():
    return datetime.now(timezone.utc).isoformat()


def dependency_identity():
    from . import repair_forward
    from research import analysis_freeze
    from research_program import future_validation
    from v2 import model as strength
    root = Path(__file__).resolve().parents[1]
    modules = (__file__, repair.__file__, repair_forward.__file__, strength.__file__,
               analysis_freeze.__file__, future_validation.__file__)
    return {str(Path(path).resolve().relative_to(root)): repair.sha(path) for path in modules}


def index_xg(records, history):
    """Join on actual fixture identity, never names or array position."""
    canonical = {r['match_id']: r for r in history}
    indexed = {}
    for record in records:
        mid = record['match_id']
        if mid in indexed or mid not in canonical:
            raise ValueError('unknown or duplicate xG fixture')
        row = canonical[mid]
        keys = ('home', 'away', 'cohort', 'home_goals', 'away_goals')
        if (any(row[k] != record[k] for k in keys)
                or aware(row['kickoff_at']) != aware(record['kickoff_at'])):
            raise ValueError('xG fixture identity/score mismatch')
        indexed[mid] = record
    return indexed


def target_values(rows, indexed, arm):
    values = np.array([[r['home_goals'], r['away_goals']] for r in rows], float)
    available = np.zeros(values.shape, bool)
    weight = arm['weight']
    if not 0 <= weight <= 1 or (weight and arm['field'] not in FIELDS):
        raise ValueError('unsupported blend specification')
    for i, row in enumerate(rows):
        record = indexed.get(row['match_id'], {})
        pair = record.get('values', {}).get(arm['field']) or [None, None]
        for side, value in enumerate(pair):
            if value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('xG must be a finite nonnegative value or missing')
            available[i, side] = True
            values[i, side] = (1-weight)*values[i, side] + weight*value
    return values, {'training_rows': len(rows), 'xg_sides': int(available.sum()),
                    'xg_both_sides_rows': int(available.all(axis=1).sum()),
                    'goal_fallback_sides': int((~available).sum()),
                    'xg_used_sides': int(available.sum()) if weight else 0}


def fit(rows, indexed, arm, *, half_life=730., ridge=8.):
    """Same regularized Poisson equations as the goal baseline; fractional y.

    There is no promotion-size prerequisite for research fitting. Evaluation
    always uses the original integer scores, never these pseudo targets.
    """
    rows = validate_history(rows)
    if not rows or half_life <= 0 or ridge < 0:
        raise ValueError('nonempty history and valid regularization required')
    targets, coverage = target_values(rows, indexed, arm)
    teams = sorted({r[k] for r in rows for k in ('home', 'away')})
    lookup = {team: i for i, team in enumerate(teams)}
    n = len(teams)
    h = np.array([lookup[r['home']] for r in rows])
    a = np.array([lookup[r['away']] for r in rows])
    hg, ag = targets.T
    dates = [datetime.fromisoformat(r['date']) for r in rows]
    weight = np.array([2**(-(max(dates)-d).days/half_life) for d in dates])
    home = np.array([0. if r.get('neutral') else 1. for r in rows])

    def objective(v):
        attack = v[2:2+n]-v[2:2+n].mean()
        defence = v[2+n:]-v[2+n:].mean()
        lh = np.exp(v[0]+v[1]*home+attack[h]+defence[a])
        la = np.exp(v[0]+attack[a]+defence[h])
        loss = np.sum(weight*(lh-hg*np.log(lh)+la-ag*np.log(la)))
        loss += ridge*(attack@attack+defence@defence)
        rh, ra = weight*(lh-hg), weight*(la-ag)
        ga = np.bincount(h, weights=rh, minlength=n)+np.bincount(a, weights=ra, minlength=n)+2*ridge*attack
        gd = np.bincount(a, weights=rh, minlength=n)+np.bincount(h, weights=ra, minlength=n)+2*ridge*defence
        return loss, np.r_[sum(rh+ra), sum(rh*home), ga-ga.mean(), gd-gd.mean()]

    initial = np.zeros(2+2*n)
    initial[0] = math.log(max(ag.mean(), .1))
    initial[1] = math.log(max(hg.mean(), .1)/max(ag.mean(), .1))
    result = minimize(objective, initial, jac=True, method='L-BFGS-B',
                      bounds=[(-2, 1.5), (-.8, .8)]+[(-1.5, 1.5)]*(2*n),
                      options={'maxiter': 500, 'ftol': 1e-10})
    if not result.success:
        raise ValueError('strength optimization failed: '+result.message)
    v = result.x
    return {'cohort': rows[0]['cohort'], 'intercept': float(v[0]),
            'home_advantage': float(v[1]),
            'attack': dict(zip(teams, map(float, v[2:2+n]-v[2:2+n].mean()))),
            'defence': dict(zip(teams, map(float, v[2+n:]-v[2+n:].mean()))),
            'train_end': rows[-1]['date'], 'train_rows': len(rows),
            'half_life_days': half_life, 'ridge': ridge, 'blend': arm,
            'coverage': coverage, 'history_hash': digest(rows)}


def training_before(history, target):
    return [r for r in history if r['cohort'] == target['cohort'] and r['date'] < target['date']]


def prediction(core, fixture, version, artifact_sha256=None):
    if core['cohort'] != fixture['cohort'] or core['train_end'] >= fixture['date']:
        raise ValueError('training cohort/date intersects target')
    home, away, missing = rates(core, fixture)
    matrix = repair.probability_matrix(home, away)
    primary = repair.select_score(matrix)
    h, a = np.indices(matrix.shape)
    wdl = dict(zip(repair.WDL, map(float, (matrix[h>a].sum(), matrix[h==a].sum(), matrix[h<a].sum()))))
    return attach_total({'version': version, 'input': fixture, 'generated_at': now(),
        'matrix': matrix.tolist(), 'matrix_hash': digest(matrix.tolist()),
        'primary': '-'.join(map(str, primary)), 'primary_probability': float(matrix[tuple(primary)]),
        'wdl': wdl, 'wdl_reference': max(wdl, key=wdl.get), 'expected_goals': [home, away],
        'artifact_sha256': artifact_sha256, 'training_data_cutoff': core['train_end'],
        'unseen_teams_using_fitted_global_prior': missing, 'training_coverage': core['coverage'],
        'formal_kpi_eligible': False, 'active_route': False,
        'primary_scope': 'STATISTICAL_EXPERIMENT_NOT_COMPREHENSIVE_RECOMMENDATION'})


def historical_record(core, row, quote_fixture):
    # These predictions are generated now. Original quote timestamps describe
    # the archived market, not a fictitious historical prediction timestamp.
    fixture = dict(row)
    if quote_fixture:
        if (any(quote_fixture[k] != row[k] for k in ('match_id', 'home', 'away', 'cohort'))
                or aware(quote_fixture['kickoff_at']) != aware(row['kickoff_at'])):
            raise ValueError('archived quote fixture mismatch')
        fixture.update(total_quote=quote_fixture.get('total_quote'), cutoff_at=quote_fixture['cutoff_at'])
    p = prediction(core, fixture, core['blend']['id'])
    matrix = np.asarray(p['matrix'])
    record = repair.record_prediction(matrix, row, 'joint_map', base_rates=p['expected_goals'])
    record.update(training_cutoff=core['train_end'], training_coverage=core['coverage'],
                  research_kind='KNOWN_HISTORY_DATE_ROLLING', total=p['total'],
                  total_quote_audit=p['total_quote_audit'], total_settlement=None)
    if p['total']:
        point = np.zeros(matrix.shape)
        point[row['home_goals'], row['away_goals']] = 1.
        settlement = total_settlement(point, p['total']['quote']['line'], p['total']['reference_direction'])
        record['total_settlement'] = next(key for key, value in settlement.items() if value == 1.)
    return record


def summarize(records):
    summary = repair.summarize(records)
    counts = Counter(r['total_settlement'] for r in records if r['total_settlement'])
    summary.update(actual_line_n=sum(counts.values()), actual_line_settlements=dict(counts),
                   actual_line_status='ARCHIVED_QUOTES_RETROSPECTIVE_MODEL' if counts else 'NO_ARCHIVED_QUOTES')
    return summary


def run(protocol, history, xg_records, quotes, out):
    indexed = index_xg(xg_records, history)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    quote_map = {r['match_id']: r for r in quotes}
    report = {'schema': 'WCPP-NATIONS-XG-RESULTS-1', 'created_at': now(),
              'protocol_sha256': digest(protocol), 'historical_asof_certified': False,
              'selection_window': protocol['selection_window'], 'diagnostic_window': protocol['diagnostic_window'],
              'levels': {}, 'arms': protocol['arms']}
    routes = {}
    dependencies = dependency_identity()
    version_identity = digest({'protocol': digest(protocol), 'dependencies': dependencies})[:12]
    for level in sorted({r['league_level'] for r in history}):
        cohort = [r for r in history if r['league_level'] == level]
        targets = [r for r in cohort if protocol['selection_window'][0] <= r['date'] < protocol['diagnostic_window'][1]]
        arm_rows, metrics, routes[level] = {}, {}, {}
        for arm in protocol['arms']:
            records, core, fitted_date = [], None, None
            for row in targets:
                if row['date'] != fitted_date:
                    core = fit(training_before(cohort, row), indexed, arm, **protocol['fit'])
                    fitted_date = row['date']
                q = quote_map.get(row['match_id'])
                if q and any(q[k] != row[k] for k in ('home', 'away', 'cohort')):
                    raise ValueError('archived quote fixture mismatch')
                records.append(historical_record(core, row, q))
            arm_rows[arm['id']] = records
            metrics[arm['id']] = {name: summarize([r for r in records if bounds[0] <= r['date'] < bounds[1]])
                                 for name, bounds in [('selection', protocol['selection_window']), ('diagnostic', protocol['diagnostic_window'])]}
            repair.write(out/level/(arm['id']+'-predictions.json'), records)
            final = fit(cohort, indexed, arm, **protocol['fit'])
            artifact = {'schema': 'WCPP-NATIONS-XG-ARTIFACT-1', 'created_at': now(),
                'version': f'NATIONS-XG-{level}-{arm["id"]}-{version_identity}',
                'core': final, 'protocol_sha256': digest(protocol),
                'inputs': protocol['inputs'], 'engine_sha256': repair.sha(__file__),
                'dependencies': dependencies,
                'historical_asof_certified': False, 'active': False}
            path = out/level/(arm['id']+'-artifact.json')
            repair.write(path, artifact)
            routes[level][arm['id']] = {'path': str(path.resolve()), 'sha256': repair.sha(path), 'version': artifact['version']}
        def rank(arm):
            result = metrics[arm['id']]['selection']
            return (-result.get('primary_hits', 0), -result.get('wdl_hits', 0),
                    result.get('score_log_loss', float('inf')), arm['weight'], arm['id'])
        selected = min(protocol['arms'], key=rank)['id']
        report['levels'][level] = {'selected_on_october': selected, 'metrics': metrics,
            'paired_selected_vs_goals': {name: repair.paired(
                [r for r in arm_rows['goals'] if bounds[0] <= r['date'] < bounds[1]],
                [r for r in arm_rows[selected] if bounds[0] <= r['date'] < bounds[1]])
                for name, bounds in [('selection', protocol['selection_window']), ('diagnostic', protocol['diagnostic_window'])]}}
    repair.write(out/'results.json', report)
    repair.write(out/'index.json', {'protocol_sha256': digest(protocol), 'routes': routes,
                                   'selected': {k: v['selected_on_october'] for k, v in report['levels'].items()}})
    return report


def freeze_forward(index, fixtures, out, freeze_root='/workspace/WCPP/forecasts/issued-analyses'):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    if len({f['match_id'] for f in fixtures}) != len(fixtures) or not fixtures:
        raise ValueError('unique future population required')
    track = 'nations-xg-comparison:'+index['protocol_sha256'][:12]
    prepared = []
    for original in fixtures:
        fixture = {**original, 'cutoff_at': now()}
        if aware(fixture['kickoff_at']) <= aware(fixture['cutoff_at']):
            raise ValueError('future fixture required')
        predictions = {}
        for arm, ref in index['routes'][fixture['league_level']].items():
            artifact = repair.read(ref['path'])
            if (repair.sha(ref['path']) != ref['sha256']
                    or artifact['engine_sha256'] != repair.sha(__file__)
                    or artifact.get('dependencies') != dependency_identity()
                    or artifact['protocol_sha256'] != index['protocol_sha256']):
                raise ValueError('artifact/engine/protocol mismatch')
            predictions[arm] = prediction(artifact['core'], fixture, artifact['version'], ref['sha256'])
        prepared.append((fixture, predictions))
    plan = {'schema': 'WCPP-NATIONS-XG-FORWARD-1', 'created_at': now(), 'capture_track': track,
            'fixtures_identity': [f for f, _ in prepared], 'index': index,
            'selected_on_october': index['selected'], 'first_version_only': True,
            'automatic_promotion': False, 'primary_scope': 'STATISTICAL_RESEARCH'}
    shell = {'match_id': 'nations-xg-forward:'+index['protocol_sha256'], 'cutoff_at': now(),
             'kickoff_at': min((f['kickoff_at'] for f, _ in prepared), key=aware)}
    receipt = save(plan, shell, stage='review', track='research-preregistration', root=freeze_root)
    repair.write(out/'plan.json', verify(repair.read(receipt['path'])))
    repair.write(out/'plan-receipt.json', receipt)
    plan_hash = receipt['sha256']
    receipts = []
    for fixture, predictions in prepared:
        receipt = save({'predictions': predictions, 'plan_sha256': plan_hash},
                       fixture, stage='prematch', track=track, root=freeze_root,
                       idempotency_key=index['protocol_sha256']+':'+fixture['match_id'])
        repair.write(out/'freezes'/(fixture['match_id'].replace(':', '-')+'.json'), verify(repair.read(receipt['path'])))
        receipts.append(receipt)
    repair.write(out/'receipts.json', receipts)
    return {'population': len(prepared), 'frozen': len(receipts),
            'quoted_population': sum(p['goals']['total'] is not None for _, p in prepared)}


def review_forward(bundle, results):
    """Settle the first actual freezes with the existing paired review rules."""
    import sys
    from .repair_forward import identity, validate_result
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'v5'))
    from v5.paired_review import review as paired_review
    bundle = Path(bundle)
    plan_record = verify(repair.read(bundle/'plan.json'))
    plan = plan_record['payload']
    expected = {f['match_id']: f for f in plan['fixtures_identity']}
    frozen = {}
    for path in sorted((bundle/'freezes').glob('*.json')):
        record = verify(repair.read(path))
        mid = record['match_id']
        if (mid not in expected or mid in frozen or record['revision'] != 1
                or record['stage'] != 'prematch' or record['track'] != plan['capture_track']
                or record['payload']['plan_sha256'] != plan_record['record_sha256']
                or identity(record['fixture']) != identity(expected[mid])
                or aware(record['recorded_at']) >= aware(record['fixture']['kickoff_at'])):
            raise ValueError('forward fixture/first freeze mismatch')
        frozen[mid] = record
    if set(frozen) != set(expected):
        raise ValueError('restore the complete first-freeze population before review')
    for result in results:
        validate_result(result, expected[result['match_id']])
    pairs = []
    for mid, fixture in expected.items():
        predictions = frozen[mid]['payload']['predictions']
        selected = plan['selected_on_october'][fixture['league_level']]
        pairs.append({'match_id': mid, 'fixture': fixture,
                      'current_model': predictions['goals'], 'test_model': predictions[selected]})
    return {'schema': 'WCPP-NATIONS-XG-FORWARD-REVIEW-1',
            'selected_on_october': plan['selected_on_october'],
            'comparison': paired_review(pairs, results), 'automatic_promotion': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    protocol = repair.read(args.protocol)
    inputs = {}
    for name, ref in protocol['inputs'].items():
        if repair.sha(ref['path']) != ref['sha256']:
            raise ValueError('experiment input checksum changed: '+name)
        inputs[name] = repair.read(ref['path'])
    run(protocol, inputs['history'], inputs['xg']['rows'], inputs['quotes'], args.out)


if __name__ == '__main__':
    main()
