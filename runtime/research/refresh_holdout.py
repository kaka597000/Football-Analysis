"""Fixed-recipe data refresh with a whole-date retrospective holdout.

This entry evaluates frozen original models against separately fitted candidates.
It never rewrites active assets, tunes weights, or claims historical availability.
"""
import argparse
import copy
from datetime import date, datetime, timezone
import gzip
import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import fixed_recipe_refresh as refresh

COHORTS = ('eng.1', 'ita.1', 'swe.1')
MODE = 'RETROSPECTIVE_LABEL_TIME_SPLIT_NOT_HISTORICAL_ASOF_NOT_PROSPECTIVE'


def source_keys(row):
    """Retain legacy provider namespaces; do not invent ESPN score provenance."""
    keys = {row.get(name, {}).get('source_key') for name in ('score_evidence', 'api_evidence')}
    keys.discard(None)
    ev = row.get('neutral_evidence', {})
    parsed = urlparse(ev.get('source_url', ''))
    if parsed.hostname == 'site.api.espn.com' and parsed.path == '/apis/site/v2/sports/soccer/'+row['cohort']+'/summary':
        ids = parse_qs(parsed.query).get('event', [])
        if len(ids) == 1 and ids[0].isdigit(): keys.add('espn:'+ids[0])
    return keys


def merge_incremental(old, additions, cohort, cutoff, raw_root):
    """Support registered 5Dollar history without rewriting old row dictionaries."""
    original = copy.deepcopy(old)
    old = refresh.legacy.history(copy.deepcopy(old))
    if any(r['cohort'] != cohort for r in old): raise ValueError('old cohort mismatch')
    seen_ids, seen_sources, seen_fixtures = {}, {}, {}
    fields = ('cohort', 'date', 'home', 'away', 'home_goals', 'away_goals', 'score_basis', 'neutral')
    merged = []
    def add(row, legacy_row=False):
        sources = source_keys(row)
        fixture = (row['date'], row['home'], row['away'])
        collisions = ([seen_ids[row['match_id']]] if row['match_id'] in seen_ids else [])
        collisions += [seen_sources[key] for key in sources if key in seen_sources]
        if fixture in seen_fixtures: collisions.append(seen_fixtures[fixture])
        if collisions:
            if any(tuple(prior[k] for k in fields) != tuple(row[k] for k in fields) for prior in collisions):
                raise ValueError('duplicate identity/score conflict')
            if legacy_row: raise ValueError('duplicate original history')
            return
        seen_ids[row['match_id']] = row; seen_fixtures[fixture] = row
        for key in sources: seen_sources[key] = row
        merged.append(copy.deepcopy(row))
    for row in old: add(row, True)
    for row in additions:
        refresh.validate_new(row, cohort, cutoff, raw_root)
        add(row)
    if len(merged) == len(old): raise ValueError('refresh contains no new rows')
    if any(seen_ids[row['match_id']] != row for row in original):
        raise ValueError('original row mutated')
    return refresh.legacy.history(merged)


def split_dates(rows):
    """Choose the fixed split from dates alone, keeping all same-day games together."""
    dates = sorted({r['date'] for r in rows})
    if len(dates) < 2:
        raise ValueError('at least two distinct new match dates required')
    edge = dates[len(dates) // 2]
    fit = sorted((r for r in rows if r['date'] < edge), key=lambda r: (r['date'], r['match_id']))
    holdout = sorted((r for r in rows if r['date'] >= edge), key=lambda r: (r['date'], r['match_id']))
    assert_disjoint(fit, holdout)
    return fit, holdout


def assert_disjoint(history, targets):
    if not history or not targets or max(r['date'] for r in history) >= min(r['date'] for r in targets):
        raise ValueError('training must strictly precede the entire holdout')
    keys = (lambda r: r['match_id'],
            lambda r: (r['cohort'], r['date'], r['home'], r['away']))
    for key in keys:
        if {key(r) for r in history} & {key(r) for r in targets}:
            raise ValueError('training/holdout identity overlap')
    if set().union(*(source_keys(r) for r in history)) & set().union(*(source_keys(r) for r in targets)):
        raise ValueError('training/holdout source identity overlap')


def summarize_matrix(matrix, row):
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or not np.isfinite(m).all() or (m < 0).any() or abs(m.sum()-1) > 1e-9:
        raise ValueError('invalid probability matrix')
    h, a = row['home_goals'], row['away_goals']
    if not (0 <= h < m.shape[0] and 0 <= a < m.shape[1]):
        raise ValueError('label outside modeled score support')
    primary = tuple(map(int, np.unravel_index(np.argmax(m), m.shape)))
    ii, jj = np.indices(m.shape)
    wdl = np.array([m[ii > jj].sum(), m[ii == jj].sum(), m[ii < jj].sum()])
    actual = 0 if h > a else 1 if h == a else 2
    target = np.eye(3)[actual]
    return {'status': 'SCORED', 'primary': list(primary), 'primary_hit': primary == (h, a),
            'wdl': wdl.tolist(), 'wdl_pick': int(np.argmax(wdl)),
            'wdl_hit': int(np.argmax(wdl)) == actual,
            'score_log_loss': -math.log(max(float(m[h, a]), 1e-300)),
            'wdl_brier': float(((wdl-target)**2).sum()),
            'wdl_rps': float(((np.cumsum(wdl)[:2]-np.cumsum(target)[:2])**2).sum()/2),
            'matrix': m.tolist(), 'matrix_sha256': refresh.legacy.digest(m.tolist())}


def numerical_pair(current, test, fixture):
    """Use the original execution recipes; keep genuine-future guards untouched."""
    if fixture.get('neutral') is not False:
        raise ValueError('baseline neutral venue unsupported or unknown')
    anchor = current['anchor']
    if (current['code_hash'] != refresh.weighted.code_hash()
            or current['config'] != refresh.weighted.CONFIG
            or anchor['code_hash'] != refresh.legacy.code_hash()):
        raise ValueError('original engine/config contract mismatch')
    if anchor.get('context_coefficients'):
        raise ValueError('trained context requires a separately audited replay')
    if max(r['date'] for r in anchor['history']) >= fixture['date']:
        raise ValueError('training reaches target calendar date')
    base, meta = refresh.legacy.distribution(anchor['core'], anchor['form'],
                                             anchor['history'], fixture, anchor['execution_recipe'])
    # The real current-model entry rejects these cases; do not score its fallback.
    if meta['unseen_teams']:
        raise ValueError('unseen teams unsupported for main-model prediction')
    output = {'current': summarize_matrix(base, fixture)}
    output['current']['unseen_teams'] = meta['unseen_teams']
    try:
        if (test['code_hash'] != refresh.ml.code_hash() or test['config'] != refresh.ml.CONFIG
                or test['feature_names'] != refresh.ml.FEATURES):
            raise ValueError('test engine/config contract mismatch')
        if max(r['date'] for r in test['history']) >= fixture['date']:
            raise ValueError('test training reaches target calendar date')
        rates = refresh.legacy.rates(test['core']['long'], fixture)[:2]
        features = refresh.ml.feature_matrix(test['history'], fixture, rates)
        booster = refresh.ml.lgb.Booster(model_str=test['booster_model'])
        residual, _ = refresh.ml.ml_matrix(booster, features, rates, test['core']['long']['nuisance']['dixon_coles'])
        dc, test_meta = refresh.legacy.distribution(test['core'], None, test['history'], fixture, 'dc365')
        mixture = (1-test['alpha'])*dc + test['alpha']*residual
        output['test'] = summarize_matrix(mixture, fixture)
        output['test']['unseen_teams'] = test_meta['unseen_teams']
    except (ValueError, KeyError) as exc:
        output['test'] = {'status': 'ABSTAIN', 'reason': str(exc)}
    return output


def metrics(records, model):
    good = [r[model] for r in records if r[model]['status'] == 'SCORED']
    n, scored = len(records), len(good)
    primary = sum(r['primary_hit'] for r in good)
    wdl = sum(r['wdl_hit'] for r in good)
    return {'n': n, 'scored': scored, 'abstained': n-scored, 'coverage': scored/n if n else None,
            'primary_hits': primary, 'primary_rate_all_targets': primary/n if n else None,
            'primary_rate_scored': primary/scored if scored else None,
            'wdl_hits': wdl, 'wdl_rate_all_targets': wdl/n if n else None,
            'wdl_rate_scored': wdl/scored if scored else None,
            **{k: float(np.mean([r[k] for r in good])) if scored else None
               for k in ('score_log_loss', 'wdl_brier', 'wdl_rps')},
            'actual_market_totals': {'status': 'NOT_EVALUATED_NO_VERIFIED_PREMATCH_QUOTES', 'n': 0}}


def paired_delta(records, old, new):
    paired = [r for r in records if r[old]['status'] == r[new]['status'] == 'SCORED']
    if not paired:
        return {'paired_n': 0, 'all_targets': len(records), 'status': 'NO_COMMON_PREDICTIONS'}
    dates = sorted({r['date'] for r in paired})
    counts = np.array([sum(r['date'] == d for r in paired) for d in dates])
    sums = np.array([sum(int(r[new]['primary_hit'])-int(r[old]['primary_hit'])
                         for r in paired if r['date'] == d) for d in dates])
    sampled = np.random.default_rng(20261110).integers(0, len(dates), (10000, len(dates)))
    means = sums[sampled].sum(axis=1)/counts[sampled].sum(axis=1)
    return {'paired_n': len(paired), 'all_targets': len(records), 'date_blocks': len(dates),
            'old_wrong_new_right': sum(not r[old]['primary_hit'] and r[new]['primary_hit'] for r in paired),
            'old_right_new_wrong': sum(r[old]['primary_hit'] and not r[new]['primary_hit'] for r in paired),
            'primary_net_hits': int(sums.sum()), 'primary_difference': float(sums.sum()/len(paired)),
            'primary_date_block_bootstrap95': np.quantile(means, [.025, .975]).tolist(),
            'bootstrap_interpretation': 'DESCRIPTIVE_SMALL_SAMPLE_NOT_MULTIPLICITY_ADJUSTED',
            'wdl_net_hits': sum(int(r[new]['wdl_hit'])-int(r[old]['wdl_hit']) for r in paired),
            **{k+'_difference': float(np.mean([r[new][k]-r[old][k] for r in paired]))
               for k in ('score_log_loss', 'wdl_brier', 'wdl_rps')}, 'automatic_promotion': False}


def refit_pair(originals, histories, report_hash):
    built = datetime.now(timezone.utc).isoformat()
    current = refresh.current_refit(originals['current'], histories['current'], built, report_hash)
    test, training = refresh.ml_refit(originals['test'], histories['test'], built, report_hash)
    for model in (current, test):
        model['acceptance_scope'] = 'ORIGINAL_ARTIFACT_ONLY_NOT_VALIDATION_OF_REFRESHED_MODEL'
        model['refresh_experiment'] = {'protocol': 'WCPP-FOUR-COHORT-REFRESH-20261110-1',
                                      'active': False, 'historical_asof_eligible': False}
    if current['weights'] != originals['current']['weights'] or test['alpha'] != originals['test']['alpha']:
        raise ValueError('fixed recipe changed')
    return {'current': current, 'test': test}, training


def run(args):
    out = Path(args.out); out.mkdir(parents=True, exist_ok=False)
    protocol = refresh.read(args.protocol)
    if protocol['evaluation_mode'] != MODE or protocol['automatic_promotion'] is not False:
        raise ValueError('unexpected protocol')
    for path, checksum in protocol['active_hashes'].items():
        if refresh.sha256(path) != checksum: raise ValueError('active input changed since protocol lock')
    cutoff = datetime.now(timezone.utc)
    registry = refresh.read(args.registry)
    incoming = refresh.read(args.rows)
    if isinstance(incoming, dict): incoming = incoming['rows']
    coverage = refresh.read(args.coverage)
    result = {'mode': MODE, 'protocol_sha256': refresh.sha256(args.protocol),
              'source_sha256': refresh.sha256(__file__), 'rows_sha256': refresh.sha256(args.rows),
              'coverage_sha256': refresh.sha256(args.coverage), 'cohorts': {},
              'automatic_promotion': False, 'statistical_reference_not_comprehensive_primary': True}
    for co in COHORTS:
        route = registry['models'][co]
        originals = {k: refresh.read(route[p+'artifact_path']) for k, p in (('current', ''), ('test', 'test_'))}
        if originals['current']['history'] != originals['current']['anchor']['history']:
            raise ValueError('current container/anchor history mismatch')
        rows, rejected, duplicate = [], [], []
        seen = ({}, {}, {})
        for row in incoming:
            if row.get('cohort') != co: continue
            try:
                refresh.validate_new(row, co, cutoff, args.raw_root)
                if not protocol['source_start_utc'][co] <= row['date'] <= protocol['end_date_utc']:
                    raise ValueError('outside locked incremental window')
            except (ValueError, KeyError, TypeError, OSError) as exc:
                rejected.append({'match_id': row.get('match_id'), 'reason': str(exc)}); continue
            keys = (row['match_id'], refresh.event_key(row), (row['date'], row['home'], row['away']))
            collisions = [mapping[key] for mapping, key in zip(seen, keys) if key in mapping]
            if collisions:
                if any(refresh.identity(previous) != refresh.identity(row) for previous in collisions):
                    raise ValueError('conflicting incremental identities/labels; no model fitted')
                duplicate.append(row['match_id']); continue
            for mapping, key in zip(seen, keys): mapping[key] = row
            rows.append(row)
        refresh.write_new(out/co/'row-audit.json', {'accepted': len(rows), 'rejected': rejected, 'duplicates': duplicate})
        covered = refresh.coverage_check(coverage, co, date.fromisoformat(protocol['source_start_utc'][co]),
                                         date.fromisoformat(protocol['end_date_utc']), rows)
        fit, targets = split_dates(rows)
        histories = {k: merge_incremental(originals[k]['history'], fit, co, cutoff, args.raw_root)
                     for k in ('current', 'test')}
        for k in histories: assert_disjoint(histories[k], targets)
        plan = {'cohort': co, 'fit_new_ids': [r['match_id'] for r in fit],
                'holdout_ids': [r['match_id'] for r in targets],
                'fit_end': fit[-1]['date'], 'holdout_start': targets[0]['date'],
                'holdout_end': targets[-1]['date'], 'old_alpha': originals['test']['alpha'],
                'old_weights': originals['current']['weights'],
                'current_recipe': originals['current']['anchor']['execution_recipe'],
                'mode': MODE, 'created_at': datetime.now(timezone.utc).isoformat()}
        refresh.write_new(out/co/'evaluation-plan.json', plan)
        fitted, training = refit_pair(originals, histories, refresh.legacy.digest(plan))
        for name, artifact in fitted.items():
            artifact['refresh_experiment'].update(coverage_status=covered['status'],
                historical_holdout_artifact=True, includes_holdout_labels=False)
            refresh.write_new(out/co/'evaluation'/f'{name}-artifact.json', artifact)
        records = []
        for target in targets:
            rec = {k: target[k] for k in ('match_id', 'cohort', 'date', 'home', 'away', 'home_goals', 'away_goals')}
            for prefix, pair in (('original', originals), ('refreshed', fitted)):
                try: predictions = numerical_pair(pair['current'], pair['test'], target)
                except (ValueError, KeyError) as exc:
                    predictions = {k: {'status': 'ABSTAIN', 'reason': str(exc)} for k in ('current', 'test')}
                for k, v in predictions.items(): rec[prefix+'_'+k] = v
            records.append(rec)
        with gzip.open(out/co/'holdout-predictions.json.gz', 'wt') as f:
            json.dump(records, f, ensure_ascii=False, allow_nan=False)
        report = {'cohort': co, 'mode': MODE, 'plan': plan, 'coverage': covered,
                  'accepted_new_rows': len(rows), 'rejected_new_rows': rejected,
                  'metrics': {k: metrics(records, k) for k in ('original_current', 'refreshed_current', 'original_test', 'refreshed_test')},
                  'paired_comparisons': {k: paired_delta(records, 'original_'+k, 'refreshed_'+k) for k in ('current', 'test')},
                  'ml_fit': training, 'old_history_raw_recertified': False,
                  'new_features_added': False, 'automatic_promotion': False,
                  'historical_availability': {'first_known_at_certified': False,
                      'new_fit_rows_observed_after_first_target': sum(refresh.legacy.aware(r['observed_at']).date().isoformat() >= targets[0]['date'] for r in fit),
                      'original_built_at': {k: originals[k]['built_at'] for k in originals}}}
        refresh.write_new(out/co/'holdout-report.json', report)
        # Deployment research copies include the holdout AFTER evaluation. They
        # are never substituted for the evaluation artifacts or scored above.
        full_histories = {k: merge_incremental(originals[k]['history'], rows, co, cutoff, args.raw_root) for k in originals}
        final, final_training = refit_pair(originals, full_histories, refresh.legacy.digest(report))
        for name, artifact in final.items():
            artifact['refresh_experiment'].update(coverage_status=covered['status'],
                historical_holdout_artifact=False, includes_holdout_labels=True)
            refresh.write_new(out/co/'final'/f'{name}-artifact.json', artifact)
        refresh.write_new(out/co/'final'/'training.json', final_training)
        result['cohorts'][co] = {'report': str(out/co/'holdout-report.json'), 'train_end': full_histories['current'][-1]['date'],
                                'new_rows': len(rows), 'fit_new_rows': len(fit), 'holdout_n': len(targets),
                                'coverage': covered['status'], 'active': False}
        print(json.dumps({'completed': co, **result['cohorts'][co]}), flush=True)
    result['active_hashes_unchanged'] = all(refresh.sha256(p) == h for p, h in protocol['active_hashes'].items())
    if not result['active_hashes_unchanged']: raise ValueError('active input changed during run')
    refresh.write_new(out/'manifest.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', default='/workspace/WCPP/runtime/active-model.json')
    for field in ('protocol', 'rows', 'coverage', 'raw-root', 'out'): parser.add_argument('--'+field, required=True)
    run(parser.parse_args())
