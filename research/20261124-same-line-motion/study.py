"""Fit on earlier labels, hash both forecasts, then grade the sealed pairs."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from math import log
from pathlib import Path

import numpy as np

from optimization.evaluate import settle_ou
from research.analysis_freeze import aware
from research_program import same_line_motion as model
from research_program.three_arm_model import probabilities


HERE = Path(__file__).resolve().parent
RULES = json.loads((HERE/'protocol.json').read_bytes())
ARMS = ('baseline', 'candidate')
METRICS = ('first', 'top3', 'wdl', 'total')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def packed(obj):
    return (json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError(f'conflicting prior output at {path.name}')
    else:
        with path.open('xb') as file:
            file.write(raw)


def save(path, obj):
    write(path, (json.dumps(obj, sort_keys=True, indent=2, allow_nan=False)+'\n').encode())


def indexed(path):
    values = {}
    for raw in path.read_bytes().splitlines():
        row = json.loads(raw)
        if row['id'] in values:
            raise ValueError('duplicate event identity')
        values[row['id']] = row
    return values


def input_rows(path, panel):
    book = 'DraftKings' if panel == 'native' else 'Bet365'
    rows = indexed(path)
    for r in rows.values():
        if {'score', 'home_goals', 'away_goals', 'result'} & r.keys():
            raise ValueError('outcome in feature projection')
        if r['provider'] != book or r['split'] not in ('train', 'test', 'excluded'):
            raise ValueError('unregistered book or window')
        if r['split'] == 'excluded':
            if not r.get('excluded'):
                raise ValueError('excluded roster item needs a reason')
            continue
        if panel == 'proxy':
            day = datetime.fromisoformat(r['date'])
            if (r['split'] == 'train' and r['season'] not in RULES['proxy_fit']
                    or r['split'] == 'test' and (day.year not in (2023, 2024, 2025) or not 8 <= day.month <= 11)
                    or r['competition'] not in RULES['proxy_scope'].values()):
                raise ValueError('proxy outside registered window or scope')
        elif not r.get('excluded'):
            cutoff, ko = aware(r['cutoff_at']), aware(r['kickoff_at'])
            split = aware(RULES['native_split'])
            if (cutoff >= ko or r['split'] == 'train' and not aware(RULES['native_start']) <= cutoff < ko < split
                    or r['split'] == 'test' and not split <= cutoff < ko < aware(RULES['native_stop_exclusive'])):
                raise ValueError('original outside registered decision window')
    return rows


def observed_score(row, label, panel, training):
    if label is None or label.get('score') is None:
        return None
    score = label['score']
    if len(score) != 2 or any(type(x) is not int or x < 0 for x in score):
        raise ValueError('expected two nonnegative regulation goals')
    if panel == 'proxy':
        if any(row[k] != label[k] for k in ('id', 'date', 'home', 'away', 'competition', 'split')):
            raise ValueError('historical outcome identity conflicts with price row')
    else:
        if (not all(row.get(k) and row[k] == label.get(k) for k in ('id', 'home_id', 'away_id'))
                or aware(row['kickoff_at']) != aware(label['kickoff_at']) or not label.get('evidence_sha256')
                or aware(label['observed_at']) <= aware(row['kickoff_at'])):
            raise ValueError('certified result does not match original sides or time')
        if training and aware(label['observed_at']) >= aware(RULES['native_split']):
            return None
    return score


def fit(panel, inputs, labels, destination):
    roster, outcomes = input_rows(inputs, panel), indexed(labels)
    if any(key in roster and roster[key]['split'] == 'test' for key in outcomes):
        raise ValueError('late target in training labels')
    train = [r for r in roster.values() if r['split'] == 'train']
    terms = []
    counters = Counter()
    for row in train:
        status, _ = model.observation(row)
        counters[status] += 1
        if status == 'same_line':
            score = observed_score(row, outcomes.get(row['id']), panel, True)
            term = model.training_term(row, score)
            if term is not None:
                terms.append(term)
            elif score is None:
                counters['no_eligible_early_outcome'] += 1
            else:
                counters['push'] += 1
    result = dict(panel=panel, coefficient=model.fit(terms), fit_sources=len(terms), train_roster=len(train),
                  by_status=counters, input_sha256=sha(inputs), early_labels_sha256=sha(labels),
                  protocol_sha256=sha(HERE/'protocol.json'))
    save(destination, result)
    return result


def matrix_sha(matrix):
    return hashlib.sha256(np.ascontiguousarray(matrix, dtype='<f8').tobytes()).hexdigest()


def forecasts(row, beta):
    mats = model.paired_matrices(row, beta)
    if not np.allclose(probabilities(mats[0]), probabilities(mats[1]), rtol=0, atol=1e-12):
        raise ValueError('WDL margins changed')
    picks = [dict(**model.picks(m, row['current_total']['line']), sha256=matrix_sha(m)) for m in mats]
    # Both projections use the identical WDL target, including ties within rounding error.
    picks[1]['wdl'] = picks[0]['wdl']
    return mats, dict(zip(ARMS, picks))


def seal(panel, inputs, fitted, transfer, out):
    rows = input_rows(inputs, panel)
    artifact = json.loads(fitted.read_bytes())
    rule = sha(HERE/'protocol.json')
    if artifact['panel'] != panel or artifact['protocol_sha256'] != rule or artifact['input_sha256'] != sha(inputs):
        raise ValueError('fit source or registered panel changed')
    beta = artifact['coefficient']
    fit_panel = panel
    if beta is None:
        if panel != 'native' or transfer is None:
            raise ValueError('registered coefficient unavailable')
        fallback = json.loads(transfer.read_bytes())
        if fallback['panel'] != 'proxy' or fallback['protocol_sha256'] != rule or fallback['coefficient'] is None:
            raise ValueError('invalid predeclared transfer')
        beta, fit_panel = fallback['coefficient'], 'proxy'
    elif transfer is not None:
        raise ValueError('native fit is available; transfer not permitted')
    raw, tally, days = bytearray(), Counter(), set()
    for row in sorted(rows.values(), key=lambda r: (r['date'], r['id'])):
        if row['split'] != 'test':
            continue
        status, signal = model.observation(row)
        tally[status] += 1
        item = dict(id=row['id'], status=status, date=row['date'], competition=row['competition'])
        if signal is not None:
            _, item['forecasts'] = forecasts(row, beta)
            if status == 'same_line':
                days.add(row['date'])
        raw.extend(packed(item))
    write(out/'forecasts.jsonl', raw)
    lock = dict(panel=panel, fit_panel=fit_panel, beta=beta, listed=sum(tally.values()), status=tally,
                applicable_days=len(days), fit_sha256=sha(fitted), protocol_sha256=rule, input_sha256=sha(inputs),
                forecasts_sha256=sha(out/'forecasts.jsonl'), transfer_sha256=sha(transfer) if transfer else None)
    if panel == 'native':
        # Timestamp of protocol commit 2c57739. Results may arrive after kickoff-window close.
        lock['result_cutoff_at'] = '2026-11-24T11:26:32Z'
    save(out/'lock.json', lock)
    return lock


def interval(graded):
    clusters = defaultdict(lambda: [0, 0])
    for r, _, scores in graded:
        clusters[r['date']][0] += int(scores[1]['first']) - int(scores[0]['first'])
        clusters[r['date']][1] += 1
    if len(clusters) < 5:
        return None
    data = np.array([clusters[k] for k in sorted(clusters)])
    idx = np.random.default_rng(20261124).integers(len(data), size=(2000, len(data)))
    counts = data[idx].sum(axis=1)
    return np.quantile(counts[:,0]/counts[:,1], [.025, .975]).tolist()


def summary(rows):
    result = dict(paired=len(rows), dates=len({row['date'] for row, _, _ in rows}), first_delta_95=interval(rows))
    for metric in METRICS:
        pairs = [(picks, left[metric], right[metric]) for _, picks, (left, right) in rows
                 if left[metric] is not None and right[metric] is not None]
        result[metric] = dict(n=len(pairs), baseline=sum(a for _, a, _ in pairs), candidate=sum(b for _, _, b in pairs),
                              gained=sum(b and not a for _, a, b in pairs), lost=sum(a and not b for _, a, b in pairs),
                              picks_changed=sum(p['baseline'][metric] != p['candidate'][metric] for p, _, _ in pairs))
    result['log_loss'] = {arm: float(np.mean([s[i]['log_loss'] for _, _, s in rows])) if rows else None
                          for i, arm in enumerate(ARMS)}
    result['settlement'] = {arm: dict(Counter(s[i]['settled'] for _, _, s in rows)) for i, arm in enumerate(ARMS)}
    return result


def grade(panel, inputs, labels, lockpath, out):
    lock = json.loads(lockpath.read_bytes())
    forecasts_path = lockpath.parent/'forecasts.jsonl'
    if (lock['panel'] != panel or sha(inputs) != lock['input_sha256']
            or lock['forecasts_sha256'] != sha(forecasts_path) or lock['protocol_sha256'] != sha(HERE/'protocol.json')):
        raise ValueError('sealed input or forecasts changed')
    roster, outcomes, frozen = input_rows(inputs, panel), indexed(labels), indexed(forecasts_path)
    if set(frozen) != {k for k, r in roster.items() if r['split'] == 'test'}:
        raise ValueError('held-out roster changed')
    seen, all_pairs, eligible = Counter(), [], []
    for key, issued in frozen.items():
        row = roster[key]
        if 'forecasts' not in issued:
            seen['no_pair'] += 1
            continue
        score = observed_score(row, outcomes.get(key), panel, False)
        if score is not None and panel == 'native' and aware(outcomes[key]['observed_at']) >= aware(lock['result_cutoff_at']):
            raise ValueError('outcome observed after the registered evidence snapshot')
        if score is None:
            seen['missing_outcome'] += 1
            continue
        h, a = score
        truth = f'{h}-{a}'
        direction = 'home' if h > a else 'away' if a > h else 'draw'
        matrices, picks = forecasts(row, lock['beta'])
        if picks != issued['forecasts']:
            raise ValueError('recreated predictions differ from seal')
        pair = []
        for arm, matrix in zip(ARMS, matrices):
            v = picks[arm]
            settled = settle_ou(h+a, row['current_total']['line'], v['total'])
            prob = matrix[h,a] if h < matrix.shape[0] and a < matrix.shape[1] else 0.
            pair.append(dict(first=v['first'] == truth, top3=truth in v['top3'], wdl=v['wdl'] == direction,
                             total=None if settled == 'PUSH' else settled == 'FULL_WIN', settled=settled,
                             log_loss=-log(max(float(prob), 1e-300))))
        all_pairs.append((row, picks, pair))
        if issued['status'] == 'same_line':
            eligible.append(all_pairs[-1])
    same, total = summary(eligible), summary(all_pairs)
    groups = sorted({v['competition'] for v in frozen.values()})
    by_league = {g: dict(listed=sum(r['competition'] == g for r in frozen.values()),
                         **summary([t for t in all_pairs if t[0]['competition'] == g])) for g in groups}
    by_day = {d: summary([t for t in all_pairs if t[0]['date'] == d]) for d in sorted({t[0]['date'] for t in all_pairs})}
    gate = (panel == 'native' and lock['fit_panel'] == 'native' and same['paired'] >= 100 and same['dates'] >= 5
            and same['first_delta_95'][0] > 0 and all(same[m]['candidate'] >= same[m]['baseline']
            for m in ('top3', 'wdl', 'total')) and same['log_loss']['candidate'] <= same['log_loss']['baseline'])
    result = dict(panel=panel, fit_panel=lock['fit_panel'], beta=lock['beta'], lock_sha256=sha(lockpath),
                  labels_sha256=sha(labels), listed=len(frozen), gaps=seen, all_paired=total, same_line=same,
                  by_league=by_league, by_day=by_day, eligible_for_future_proposal=bool(gate),
                  actual_display_pairs={'first': 0, 'top3': 0, 'selected_three_batches': 0})
    if panel == 'native':
        # Project members previously reviewed this specific delivered slate. Report its contribution separately.
        result['sensitivity_excluding_previously_reviewed_slate'] = {
            'excluded_batch': '20261122-covered-slate',
            'all_paired': summary([v for v in all_pairs if v[0]['batch'] != '20261122-covered-slate']),
            'same_line': summary([v for v in eligible if v[0]['batch'] != '20261122-covered-slate'])}
    save(out, result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('fit', 'seal', 'grade'):
        p = sub.add_parser(name)
        p.add_argument('--panel', required=True, choices=('native', 'proxy'))
        p.add_argument('--inputs', required=True, type=Path)
        p.add_argument('--out', required=True, type=Path)
        if name == 'seal':
            p.add_argument('--fit', required=True, type=Path)
            p.add_argument('--transfer', type=Path)
        else:
            p.add_argument('--labels', required=True, type=Path)
        if name == 'grade':
            p.add_argument('--lock', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'fit':
        result = fit(args.panel, args.inputs, args.labels, args.out)
    elif args.action == 'seal':
        result = seal(args.panel, args.inputs, args.fit, args.transfer, args.out)
    else:
        result = grade(args.panel, args.inputs, args.labels, args.lock, args.out)
        result = {k: result[k] for k in ('panel', 'listed', 'gaps', 'all_paired', 'eligible_for_future_proposal')}
    print(json.dumps(result, sort_keys=True))
