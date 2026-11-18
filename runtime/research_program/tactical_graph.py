"""Evaluate live pass-recipient prediction on fixed StatsBomb Euro 2024 match splits.

The scorer predicts among the ten other starters at the observed passing location.
Graph messages and location estimates use only earlier passes of the same team.
No tactical output from this script enters the prematch score models.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from urllib.request import urlopen

import numpy as np
from scipy.special import log_softmax

REVISION = '4b73468fc5b0f1950f9f66fada70ad3a4f9327cb'
SOURCE = 'https://raw.githubusercontent.com/statsbomb/open-data/' + REVISION + '/'
ROSTER_STOPS = {'Substitution', 'Player Off', 'Player On'}
SEED = 20261118
ARMS = ('pair_frequency', 'node_and_dyad', 'directed_graph')


def load_source(cache, key, manifest):
    path = cache / key
    if not path.exists():
        with urlopen(SOURCE + key, timeout=30) as response:
            body = response.read()
        parsed = json.loads(body)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    else:
        body = path.read_bytes()
        parsed = json.loads(body)
    manifest[key] = sha256(body).hexdigest()
    return parsed


class PassingGraph:
    def __init__(self, lineup):
        self.index = {s['player']['id']: i for i, s in enumerate(lineup)}
        positions = np.array([p['position']['id'] for p in lineup])
        self.roles = np.eye(4)[np.digitize(positions, [2, 9, 21])]
        self.edges = np.zeros((11, 11))
        self.nloc = np.zeros(11)
        self.xy_sum = np.zeros((11, 2))
        self.active = len(self.index) == 11

    def observe(self, src, dest, origin, end):
        self.edges[src, dest] += 1
        for node, xy in ((src, origin), (dest, end)):
            if xy and len(xy) >= 2:
                self.nloc[node] += 1
                self.xy_sum[node] += xy[:2]

    def features(self, src, location):
        received, passed = self.edges.sum(0), self.edges.sum(1)
        # Prior observed locations are in StatsBomb's attacking direction, 120 x 80.
        xy = self.xy_sum / np.maximum(self.nloc, 1)[:, None] / [120, 80]
        xy[self.nloc == 0] = .5
        nodes = np.column_stack((np.log1p(received), np.log1p(passed), self.roles,
                                 xy, self.nloc > 0))
        delta = xy - np.asarray(location[:2]) / [120, 80]
        partner_features = np.column_stack((
            np.log1p(self.edges[src]), np.log1p(self.edges[:, src]), nodes,
            np.broadcast_to(nodes[src], nodes.shape), delta, np.abs(delta),
        ))
        # Weighted neighbor means are one directed message-passing layer. Padding
        # for a node with no incoming/outgoing edges has a zero neighbor vector.
        incoming = self.edges.T @ nodes / np.maximum(received, 1)[:, None]
        outgoing = self.edges @ nodes / np.maximum(passed, 1)[:, None]
        message = np.column_stack((incoming, outgoing))
        graph_features = np.column_stack((message, np.broadcast_to(message[src], message.shape)))
        candidates = np.arange(11) != src
        return (np.column_stack((partner_features, graph_features))[candidates].astype(np.float32),
                partner_features.shape[1])


def samples(events):
    graphs = {}
    features, labels = [], []
    counts = Counter()
    width = None
    for event in sorted(events, key=lambda x: x['index']):
        if event['period'] != 1:
            continue
        kind, team = event['type']['name'], event['team']['id']
        if kind == 'Starting XI':
            graphs[team] = PassingGraph(event['tactics']['lineup'])
            continue
        graph = graphs.get(team)
        if graph is None:
            raise ValueError('missing starting XI for team')
        card = (event.get('foul_committed', {}).get('card')
                or event.get('bad_behaviour', {}).get('card') or {})
        if kind in ROSTER_STOPS or card.get('name') in ('Red Card', 'Second Yellow'):
            graph.active = False
        if kind != 'Pass':
            continue
        if event.get('pass', {}).get('outcome'):
            continue
        passing = event['pass']
        regular = event['play_pattern']['name'] == 'Regular Play'
        if regular:
            counts['completed_regular_first_half'] += 1
        source = graph.index.get(event['player']['id'])
        dest = graph.index.get(passing.get('recipient', {}).get('id'))
        if not graph.active or source is None or dest is None or source == dest:
            if regular:
                counts['roster_or_recipient_unavailable'] += 1
            continue
        loc = event.get('location')
        if regular and loc and len(loc) >= 2:
            x, width = graph.features(source, loc)
            features.append(x)
            labels.append(dest - int(dest > source))
            counts['evaluated'] += 1
        elif regular:
            counts['origin_unavailable'] += 1
        graph.observe(source, dest, loc, passing.get('end_location'))
    if counts['completed_regular_first_half'] != sum(counts[k] for k in (
            'evaluated', 'origin_unavailable', 'roster_or_recipient_unavailable')):
        raise ValueError('coverage counts differ')
    return np.stack(features), np.array(labels), dict(counts), width


def train(x, y):
    """Fit twelve shared hidden units and a categorical candidate readout with Adam."""
    rng = np.random.default_rng(SEED)
    width, hidden_size = x.shape[-1], 12
    params = [rng.normal(0, 1 / np.sqrt(width), (width, hidden_size)),
              np.zeros(hidden_size), rng.normal(0, .1, hidden_size)]
    moments = [np.zeros_like(p) for p in params]
    squares = [np.zeros_like(p) for p in params]
    t = 0
    for _ in range(16):
        order = rng.permutation(len(y))
        for start in range(0, len(y), 128):
            indices = order[start:start + 128]
            block = x[indices].astype(float)
            h = np.tanh(block @ params[0] + params[1])
            diff = np.exp(log_softmax(h @ params[2], axis=1))
            diff[np.arange(len(indices)), y[indices]] -= 1
            diff /= len(indices)
            hidden_grad = diff[:, :, None] * params[2] * (1 - h * h)
            flat_grad = hidden_grad.reshape(-1, hidden_size)
            grads = [block.reshape(-1, width).T @ flat_grad + .0001 * params[0],
                     flat_grad.sum(0),
                     h.reshape(-1, hidden_size).T @ diff.reshape(-1) + .0001 * params[2]]
            t += 1
            for j, grad in enumerate(grads):
                moments[j] = .9 * moments[j] + .1 * grad
                squares[j] = .999 * squares[j] + .001 * grad ** 2
                params[j] -= .003 * (moments[j] / (1 - .9 ** t)) / (
                    np.sqrt(squares[j] / (1 - .999 ** t)) + 1e-8)
    return params


def scored(x, y, weights=None):
    if weights is None:
        # log(1 + earlier passes between these two players) is Laplace smoothing.
        logits = x[..., 0]
    else:
        w, bias, output = weights
        logits = np.tanh(x @ w + bias) @ output
    lp = log_softmax(logits, axis=1)
    return {'top1_hits': int((np.argmax(lp, axis=1) == y).sum()),
            'log_loss_sum': float(-lp[np.arange(len(y)), y].sum())}


def run(cache, out):
    manifest = {}
    matches = load_source(cache, 'data/matches/55/282.json', manifest)
    train_rows, test_rows = [], []
    for match in sorted(matches, key=lambda m: (m['match_date'], m['kick_off'], m['match_id'])):
        mid = match['match_id']
        events = load_source(cache, 'data/events/' + str(mid) + '.json', manifest)
        x, y, counts, base_width = samples(events)
        item = {'date': match['match_date'], 'id': mid, 'counts': counts, 'x': x, 'y': y}
        partition = train_rows if match['competition_stage']['name'] == 'Group Stage' else test_rows
        partition.append(item)
    if len(train_rows) != 36 or len(test_rows) != 15 or train_rows[-1]['date'] >= test_rows[0]['date']:
        raise ValueError('fixed chronological Euro 2024 split does not match the source')
    x = np.concatenate([r['x'] for r in train_rows])
    y = np.concatenate([r['y'] for r in train_rows])
    # Same width and initialization in both models. The ablation masks graph
    # messages, leaving the directed dyad and all local node features available.
    graph = train(x, y)
    x[..., base_width:] = 0
    local = train(x, y)
    by_match = []
    for row in test_rows:
        x, y = row['x'], row['y']
        graph_result = scored(x, y, graph)
        x[..., base_width:] = 0
        outcomes = [scored(x, y), scored(x, y, local), graph_result]
        by_match.append({'match_id': row['id'], 'match_date': row['date'], 'counts': row['counts'],
                         'arms': dict(zip(ARMS, outcomes))})
    total = sum(r['counts']['evaluated'] for r in by_match)
    results = {}
    for arm in ARMS:
        hits = sum(r['arms'][arm]['top1_hits'] for r in by_match)
        loss = sum(r['arms'][arm]['log_loss_sum'] for r in by_match)
        results[arm] = {'hits': hits, 'evaluated': total, 'top1_rate': hits / total, 'log_loss': loss / total}
    blocks = np.random.default_rng(SEED).integers(len(by_match), size=(2000, len(by_match)))
    delta = np.array([r['arms']['directed_graph']['top1_hits'] - r['arms']['node_and_dyad']['top1_hits']
                      for r in by_match])
    n = np.array([r['counts']['evaluated'] for r in by_match])
    interval = np.quantile(delta[blocks].sum(1) / n[blocks].sum(1), [.025, .975])
    coverage = {name: dict(sum((Counter(row['counts']) for row in part), Counter()))
                for name, part in [('train', train_rows), ('holdout', test_rows)]}
    report = {'task': 'within-match exact pass receiver conditional on a completed regular-play first-half pass',
              'inference_time': 'current passer and pass origin; directed graph uses earlier events only',
              'source': 'StatsBomb Open Data', 'source_revision': REVISION,
              'split': {'train_matches': len(train_rows), 'training_dates': [train_rows[0]['date'], train_rows[-1]['date']],
                        'test_matches': len(test_rows), 'holdout_dates': [test_rows[0]['date'], test_rows[-1]['date']]},
              'coverage': coverage, 'arms': results,
              'graph_minus_node_and_dyad': {'net_hits': int(delta.sum()), 'top1_rate_difference': float(delta.sum() / total),
                                            'match_block_bootstrap95': interval.tolist()},
              'match_metrics': by_match, 'source_sha256': manifest}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    weights = {a: [p.tolist() for p in w] for a, w in [('node_and_dyad', local), ('directed_graph', graph)]}
    (out / 'weights.json').write_text(json.dumps(weights, separators=(',', ':')) + '\n')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache', required=True, type=Path, help='local-only cache for official source files')
    p.add_argument('--out', required=True, type=Path)
    a = p.parse_args()
    result = run(a.cache, a.out)
    print(json.dumps({k: result[k] for k in ('coverage', 'arms', 'graph_minus_node_and_dyad')}, indent=2))
