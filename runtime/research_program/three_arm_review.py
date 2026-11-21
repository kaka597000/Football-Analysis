"""Review the original three-arm score distributions against retained ESPN regulation results."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from optimization.evaluate import settle_ou
from publication import atomic_json
from research.analysis_freeze import aware, digest, save, verify
from research_program.replay import _finished_espn_label
from storage_lock import exclusive_lock

ARMS = ('current', 'market', 'market_cohort')
METRICS = ('score_top1', 'wdl', 'total_strict_full_win')
SETTLEMENTS = ('FULL_WIN', 'HALF_WIN', 'PUSH', 'HALF_LOSS', 'FULL_LOSS')


def read(path):
    return json.loads(path.read_bytes())


def _first_freeze(path, track):
    record = verify(read(path))
    if (record['stage'] != 'prematch' or record['track'] != track or record['revision'] != 1
            or record['evaluation_slot'] != 'FIRST_PREMATCH'):
        raise ValueError('original prematch freeze required')
    return record


def _predictions(batch):
    plan = _first_freeze(batch / 'plan.json', 'three-arm-plan')
    fixtures = {r['match_id']: r for r in plan['payload']['fixtures']}
    if not fixtures or len(fixtures) != len(plan['payload']['fixtures']):
        raise ValueError('nonempty unique plan population required')
    captures = {}
    for path in sorted((batch / 'freezes').glob('*.json')):
        record = _first_freeze(path, 'three-arm-score')
        mid, f, payload = record['match_id'], record['fixture'], record['payload']
        if mid not in fixtures or mid in captures or payload['plan_sha256'] != plan['record_sha256']:
            raise ValueError('score freeze differs from registered population')
        registered = fixtures[mid]
        for key in ('match_id', 'cohort', 'stage', 'home', 'away'):
            if registered[key] != f[key]:
                raise ValueError('frozen fixture identity conflict: ' + key)
        kickoff, cutoff = aware(f['kickoff_at']), aware(f['cutoff_at'])
        if (aware(registered['kickoff_at']) != kickoff or aware(registered['cutoff_at']) != cutoff
                or not cutoff <= aware(plan['recorded_at']) <= aware(record['recorded_at']) < kickoff):
            raise ValueError('invalid freeze chronology')
        quote = payload['market']
        if (payload['artifact_sha256'] != plan['payload']['artifact_sha256']
                or quote != plan['payload']['markets'][mid]):
            raise ValueError('frozen market or artifact differs from registered experiment')
        if quote is not None:
            captured = aware(quote['captured_at'])
            if (quote['match_id'] != mid or not quote['bookmaker']
                    or not all(aware(quote[k]) <= captured <= cutoff for k in ('h2h_quoted_at', 'total_quoted_at'))):
                raise ValueError('invalid first-issue market identity or timestamp')
        arms = payload['arms']
        if arms and set(arms) != set(ARMS):
            raise ValueError('incomplete or unexpected arm set')
        for forecast in arms.values():
            mat = np.array(forecast['matrix'], dtype=float)
            if (digest(forecast['matrix']) != forecast['matrix_sha256'] or mat.ndim != 2 or not mat.size
                    or not np.isfinite(mat).all() or mat.min() < 0 or abs(mat.sum() - 1.) > 1e-8):
                raise ValueError('invalid frozen probability matrix')
            if forecast.get('wdl') not in ('home', 'draw', 'away'):
                raise ValueError('invalid frozen WDL')
            h, a = map(int, forecast['primary'].split('-'))
            if min(h, a) < 0 or h >= mat.shape[0] or a >= mat.shape[1] or forecast['primary'] != f'{h}-{a}':
                raise ValueError('invalid statistical first score')
            total = forecast.get('total')
            if total is not None:
                if (not quote or total['line'] != quote['total']['line']
                        or total['direction'] not in ('over', 'under')):
                    raise ValueError('frozen total differs from common market')
                settle_ou(0, total['line'], total['direction'])
        captures[mid] = record
    return plan, fixtures, captures


def _certify(row, fixture, state, now):
    if (any(row[key] != fixture[key] for key in ('match_id', 'cohort', 'stage', 'home', 'away'))
            or aware(row['kickoff_at']) != aware(fixture['kickoff_at'])):
        raise ValueError('result fixture identity conflict')
    meta = row['raw_reference']
    stamp = aware(meta['captured_at'])
    if (row['score_basis'] != 'regulation_90' or aware(row['known_at']) != stamp
            or not aware(fixture['kickoff_at']) + timedelta(minutes=90) <= stamp <= now):
        raise ValueError('result score basis or observation time conflict')
    url = urlsplit(meta['source_url'])
    league = fixture['cohort'].split(':')[0]
    if (row['source_url'] != meta['source_url'] or url.scheme != 'https'
            or not url.path.endswith('/soccer/' + league + '/summary')
            or parse_qs(url.query).get('event') != [row['match_id'].removeprefix('espn:')]):
        raise ValueError('result source endpoint differs from fixture')
    state = Path(state).resolve()
    path = (state / meta['path']).resolve()
    if not path.is_relative_to(state):
        raise ValueError('result evidence outside retained state')
    raw = gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != meta['sha256']:
        raise ValueError('result raw source checksum mismatch')
    if any(type(row.get(key)) is not int or row[key] < 0 for key in ('home_goals', 'away_goals')):
        raise ValueError('invalid regulation scores')
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / 'summary.json'
        plain.write_bytes(raw)
        _finished_espn_label(row, {'url': meta['source_url']}, plain)
    return {'home': row['home_goals'], 'away': row['away_goals'], 'source': meta}


def _results(state, fixtures, now):
    retained = defaultdict(list)
    with (state / 'normalized-results.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['match_id'] in fixtures:
                retained[row['match_id']].append(row)
    results, issues = {}, {}
    for mid, rows in retained.items():
        verified = []
        for row in rows:
            try:
                verified.append(_certify(row, fixtures[mid], state, now))
            except (OSError, EOFError, KeyError, TypeError, ValueError) as exc:
                issues.setdefault(mid, []).append(str(exc))
        if len({(r['home'], r['away']) for r in verified}) > 1:
            issues.setdefault(mid, []).append('conflicting certified regulation scores')
        elif verified:
            results[mid] = min(verified, key=lambda r: (aware(r['source']['captured_at']), r['source']['sha256']))
    return results, issues


def _outcome(arm, result):
    h, a = result['home'], result['away']
    total = arm.get('total')
    settlement = settle_ou(h + a, total['line'], total['direction']) if total else None
    mat = np.asarray(arm['matrix'])
    pr = float(mat[h, a]) if h < mat.shape[0] and a < mat.shape[1] else 0.
    probabilities = np.array([np.tril(mat, -1).sum(), np.trace(mat), np.triu(mat, 1).sum()])
    direction = 'home' if h > a else 'away' if h < a else 'draw'
    observed = np.array([direction == v for v in ('home', 'draw', 'away')])
    return {'score_top1': arm['primary'] == f'{h}-{a}', 'wdl': arm['wdl'] == direction,
            'total_settlement': settlement,
            'total_strict_full_win': None if settlement in (None, 'PUSH') else settlement == 'FULL_WIN',
            'score_log_loss': float(-np.log(max(pr, 1e-15))),
            'wdl_brier': float(np.square(probabilities - observed).sum())}


def _matchday(fixture):
    zone = 'America/Sao_Paulo' if fixture['cohort'].split(':')[0] == 'bra.1' else 'UTC'
    kickoff = aware(fixture['kickoff_at'])
    return {'kickoff_utc_date': kickoff.astimezone(timezone.utc).date().isoformat(),
            'matchday': kickoff.astimezone(ZoneInfo(zone)).date().isoformat(), 'matchday_timezone': zone}


def _paired(rows, before, after, metric):
    days = defaultdict(list)
    for row in rows:
        arms = row['outcomes']
        x, y = (arms.get(k, {}).get(metric) for k in (before, after))
        if x is not None and y is not None:
            days[row['matchday']].append(int(y) - int(x))
    flat = [v for day in days.values() for v in day]
    interval = None
    if len(days) >= 2:
        blocks = [days[d] for d in sorted(days)]
        sample = np.random.default_rng(20261118).integers(len(blocks), size=(2000, len(blocks)))
        counts = np.array([len(v) for v in blocks])
        gains = np.array([sum(v) for v in blocks])
        rates = gains[sample].sum(axis=1) / counts[sample].sum(axis=1)
        interval = np.quantile(rates, [.025, .975]).tolist()
    return {'eligible': len(flat), 'gained': flat.count(1), 'lost': flat.count(-1),
            'net_hits': sum(flat), 'net_rate': sum(flat) / len(flat) if flat else None,
            'matchday_blocks': len(days), 'date_block_bootstrap_95pct_rate': interval}


def _independent_review(path, plan, fixtures, captures, rows):
    if not path.is_file():
        return {'status': 'NOT_ISSUED', 'eligible': 0}
    record = _first_freeze(path, 'three-arm-independent-comprehensive')
    payload = record['payload']
    issued = aware(record['recorded_at'])
    if payload['plan_sha256'] != plan['record_sha256'] or issued < aware(plan['recorded_at']):
        raise ValueError('independent decision not bound to registered plan')
    by_id = {r['match_id']: r for r in rows}
    decisions, pairs, tracks = [], [], Counter()
    seen = set()
    for decision in payload['rows']:
        mid = decision['match_id']
        if mid not in fixtures or mid in seen:
            raise ValueError('independent decision has an unregistered or duplicate fixture')
        seen.add(mid)
        kickoff = aware(fixtures[mid]['kickoff_at'])
        decided = aware(decision['decided_at'])
        if not aware(plan['recorded_at']) <= decided <= issued < kickoff or aware(decision['kickoff_at']) != kickoff:
            raise ValueError('independent decision time conflicts with first issue')
        frozen = captures.get(mid)
        source = frozen['record_sha256'] if frozen else None
        if decision['score_freeze_sha256'] != source or frozen and decided < aware(frozen['recorded_at']):
            raise ValueError('independent decision differs from referenced original score freeze')
        baseline = (frozen['payload']['arms'].get('current', {}).get('primary') if frozen else None)
        choice, action = decision['selected_primary'], decision['decision']
        if decision['baseline_primary'] != baseline or action not in ('RETAIN', 'OVERRIDE', 'ABSTAIN'):
            raise ValueError('independent decision differs from baseline')
        if action == 'ABSTAIN':
            if choice is not None:
                raise ValueError('abstaining independent decision contains score')
        else:
            home, away = map(int, choice.split('-'))
            if min(home, away) < 0 or choice != f'{home}-{away}' or baseline is None or (choice == baseline) != (action == 'RETAIN'):
                raise ValueError('independent choice inconsistent with declared action')
        tracks[action] += 1
        row = by_id[mid]
        result = row['result']
        actual = f"{result['home']}-{result['away']}" if result else None
        bhit = baseline == actual if baseline is not None and result else None
        hit = choice == actual if choice is not None and result else None
        decisions.append({'match_id': mid, 'decision': action, 'baseline_primary': baseline, 'selected_primary': choice,
                          'baseline_hit': bhit, 'selected_hit': hit})
        pairs.append({'matchday': row['matchday'],
                      'outcomes': {'current': {'score_top1': bhit}, 'independent': {'score_top1': hit}}})
    hits = [r['selected_hit'] for r in decisions if r['selected_hit'] is not None]
    return {'status': 'INDEPENDENT_RESEARCH_ISSUE', 'freeze_sha256': record['record_sha256'],
            'recorded_at': record['recorded_at'], 'population': len(fixtures), 'decisions': dict(tracks),
            'unissued': len(fixtures) - len(seen), 'selected_coverage': (tracks['RETAIN'] + tracks['OVERRIDE']) / len(fixtures),
            'eligible': len(hits), 'hits': sum(hits), 'accuracy': sum(hits) / len(hits) if hits else None,
            'paired_vs_original_current': _paired(pairs, 'current', 'independent', 'score_top1'), 'rows': decisions}


def review(batch, state, now=None, comprehensive=None):
    batch, state = Path(batch), Path(state).resolve()
    now = now or datetime.now(timezone.utc)
    plan, fixtures, captures = _predictions(batch)
    if any(aware(r['recorded_at']) > now for r in (plan, *captures.values())):
        raise ValueError('review predates registered forecasts')
    results, issues = _results(state, fixtures, now)
    rows, scores = [], {}
    for mid, fixture in fixtures.items():
        record = captures.get(mid)
        arms = record['payload']['arms'] if record else {}
        result = results.get(mid)
        rows.append({'match_id': mid, **_matchday(fixture),
                     'freeze_sha256': record['record_sha256'] if record else None,
                     'forecast_status': (record['payload'].get('status', 'ISSUED') if record else 'UNISSUED'),
                     'market_available': bool(record and record['payload']['market']),
                     'result_state': 'SETTLED' if result else 'PENDING', 'result': result,
                     'source_issues': issues.get(mid, []),
                     'outcomes': {k: _outcome(v, result) for k, v in arms.items()} if result else {}})
    for role in ARMS:
        selected = [r['payload']['arms'][role] for r in captures.values() if role in r['payload']['arms']]
        evaluated = [r['outcomes'][role] for r in rows if role in r['outcomes']]
        metrics = {}
        for metric in METRICS:
            eligible = [r[metric] for r in evaluated if r[metric] is not None]
            metrics[metric] = {'n': len(eligible), 'hits': sum(eligible),
                               'accuracy': sum(eligible) / len(eligible) if eligible else None}
        categories = Counter(r['total_settlement'] for r in evaluated)
        totals = sum(a.get('total') is not None for a in selected)
        scores[role] = {'forecast_coverage': len(selected) / len(fixtures), 'forecasts': len(selected),
                        'real_line_selections': totals, 'real_line_coverage': totals / len(fixtures), 'metrics': metrics,
                        'total_settlements': {k: categories[k] for k in SETTLEMENTS},
                        'mean_score_log_loss': float(np.mean([r['score_log_loss'] for r in evaluated])) if evaluated else None,
                        'mean_wdl_brier': float(np.mean([r['wdl_brier'] for r in evaluated])) if evaluated else None}
    paired = {after + '_minus_' + before: {m: _paired(rows, before, after, m) for m in METRICS}
              for before, after in [('current', 'market'), ('current', 'market_cohort'), ('market', 'market_cohort')]}
    independent = _independent_review(Path(comprehensive) if comprehensive else batch / 'comprehensive-decisions.json',
                                      plan, fixtures, captures, rows)
    if independent.get('recorded_at') and aware(independent['recorded_at']) > now:
        raise ValueError('review predates independent research issue')
    return {'schema': 'WCPP-THREE-ARM-REVIEW-1', 'plan_sha256': plan['record_sha256'],
            'population': len(fixtures), 'issued': len(captures), 'settled': len(results), 'pending': len(fixtures) - len(results),
            'scope': 'FROZEN_STATISTICAL_TOP1', 'automatic_promotion': False,
            'uncertainty_method': 'Paired net hit rate, 2000 resamples of whole calendar matchdays. '
                                  'Brazil uses America/Sao_Paulo, other competitions use UTC. '
                                  'A 95% percentile interval requires at least two eligible matchdays.',
            'arms': scores, 'paired': paired, 'independent_research_decisions': independent, 'rows': rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', required=True, type=Path)
    p.add_argument('--state-dir', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--comprehensive', type=Path, help='optional separate first-issue decision record')
    args = p.parse_args()
    with exclusive_lock(args.batch / '.three-arm-review.lock'):
        report = review(args.batch, args.state_dir, comprehensive=args.comprehensive)
        display = args.out.resolve()
        if any(display.is_relative_to((args.batch / name).resolve()) for name in ('freezes', 'reviews')):
            raise ValueError('display output cannot be placed in a freeze store')
        if display.exists():
            previous = read(display)
            if previous.get('schema') != report['schema'] or previous.get('plan_sha256') != report['plan_sha256']:
                raise FileExistsError('display output already belongs to another record: ' + str(display))
        plan = verify(read(args.batch / 'plan.json'))
        stamps = [plan['recorded_at']] + [r['result']['source']['captured_at'] for r in report['rows'] if r['result']]
        decision_time = report['independent_research_decisions'].get('recorded_at')
        if decision_time:
            stamps.append(decision_time)
        for path in (args.batch / 'freezes').glob('*.json'):
            stamps.append(verify(read(path))['recorded_at'])
        fixture = {'match_id': 'three-arm-review:' + report['plan_sha256'], 'kickoff_at': plan['fixture']['kickoff_at'],
                   'cutoff_at': max(stamps, key=aware)}
        receipt = save(report, fixture, stage='review', track='three-arm-review', root=args.batch / 'reviews',
                       idempotency_key=digest(report))
        receipt['path'] = str(Path(receipt['path']).resolve().relative_to(args.batch.resolve()))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(args.out, dict(report, freeze_receipt=receipt))
    print(json.dumps({k: report[k] for k in ('population', 'issued', 'settled', 'pending')}))


if __name__ == '__main__':
    main()
