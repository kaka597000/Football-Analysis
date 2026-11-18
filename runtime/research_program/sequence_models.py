"""Two same-input residual models on a common, market-adjusted 90-minute score PMF."""
from datetime import date
import hashlib

import numpy as np
from scipy.special import expit

from .elo_kelly_shadow import aware, digest
from .three_arm_model import _matrix

VERSION = 'WCPP-SEQUENCE-RESIDUAL-1'
SEED = 20261118
WINDOW = 5
FIELDS = ('goals_for', 'goals_against', 'was_home', 'valid')
STEP_NAMES = tuple(team + '_' + field for team in ('own', 'opponent') for field in FIELDS)
STATIC_NAMES = ('attacking_side_is_home', 'log1p_own_baseline_mean', 'log1p_opponent_baseline_mean')
TREE_NAMES = [f't{lag}_{name}' for lag in range(-WINDOW, 0) for name in STEP_NAMES] + list(STATIC_NAMES)
RIDGE = 2.0
MAX_TILT = .35
LSTM_CONFIG = {'hidden_size': 6, 'epochs': 60, 'learning_rate': .012, 'weight_decay': .01}
TREE_CONFIG = {'rounds': 40, 'learning_rate': .05, 'num_leaves': 7,
               'min_data_in_leaf': 48, 'lambda_l2': 12.}


def _features(sequence, baseline):
    s = np.asarray(sequence, dtype=float)
    if s.shape != (2, WINDOW, len(FIELDS)) or not np.isfinite(s).all() or (s < 0).any():
        raise ValueError('two teams with five valid four-field steps required')
    if (not np.isin(s[:, :, 2:], [0., 1.]).all() or (s[:, :, :2] % 1).any()
            or np.any(s[s[:, :, 3] == 0]) or np.any(np.diff(s[:, :, 3], axis=1) < 0)):
        raise ValueError('invalid score history or left-padding mask')
    s = s.copy()
    s[:, :, :2] /= 3.
    panels = np.concatenate((s, s[::-1]), axis=2)
    means = np.array([baseline.sum(axis=1) @ np.arange(baseline.shape[0]),
                      baseline.sum(axis=0) @ np.arange(baseline.shape[1])])
    static = np.column_stack(([1., 0.], np.log1p(means), np.log1p(means[::-1])))
    return panels, static, means


def _lstm_forward(params, panels, static):
    # PyTorch LSTM gate ordering: input, forget, candidate, output.
    W, b = np.asarray(params['gate_weights']), np.asarray(params['gate_biases'])
    h = np.zeros((len(panels), W.shape[1] // 4))
    cell = np.zeros_like(h)
    for step in panels.swapaxes(0, 1):
        gates = np.concatenate((step, h), axis=1) @ W + b
        i, f, g, o = np.split(gates, 4, axis=1)
        cell = expit(f) * cell + expit(i) * np.tanh(g)
        h = expit(o) * np.tanh(cell)
    return np.column_stack((h, static)) @ np.asarray(params['output_weights']) + params['output_bias']


def _fit_lstm(panels, static, means, targets):
    import torch
    from torch import nn

    panel, context, prior, y = [torch.from_numpy(values) for values in (panels, static, means, targets)]
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(SEED)
            hidden = LSTM_CONFIG['hidden_size']
            lstm = nn.LSTM(panel.shape[2], hidden, batch_first=True, dtype=torch.float64)
            head = nn.Linear(hidden + context.shape[1], 1, dtype=torch.float64)
            nn.init.zeros_(head.weight)
            nn.init.zeros_(head.bias)
            optimizer = torch.optim.Adam(list(lstm.parameters()) + list(head.parameters()),
                                         lr=LSTM_CONFIG['learning_rate'], weight_decay=LSTM_CONFIG['weight_decay'])
            for _ in range(LSTM_CONFIG['epochs']):
                optimizer.zero_grad()
                h, _ = lstm(panel)
                d = head(torch.cat((h[:, -1], context), dim=1)).squeeze(1)
                loss = torch.mean(prior * torch.exp(d) - y * d + RIDGE * d.square() / 2)
                loss.backward()
                optimizer.step()
            with torch.no_grad():
                gates = torch.cat((lstm.weight_ih_l0, lstm.weight_hh_l0), dim=1).T
                params = {'gate_weights': gates.tolist(), 'gate_biases': (lstm.bias_ih_l0 + lstm.bias_hh_l0).tolist(),
                          'output_weights': head.weight[0].tolist(), 'output_bias': head.bias.item(),
                          'gate_order': ['input', 'forget', 'candidate', 'output'], 'torch_training_version': torch.__version__}
                h, _ = lstm(panel)
                native = head(torch.cat((h[:, -1], context), dim=1))[:, 0].numpy()
    finally:
        torch.set_num_threads(threads)
    np.testing.assert_allclose(_lstm_forward(params, panels, static), native, atol=1e-10, rtol=1e-10)
    return params


def _compact_tree(node):
    if 'leaf_value' in node:
        return node['leaf_value']
    if node['decision_type'] != '<=':
        raise ValueError('numeric LightGBM split required')
    return {'feature': node['split_feature'], 'threshold': node['threshold'],
            'left': _compact_tree(node['left_child']), 'right': _compact_tree(node['right_child'])}


def _tree_forward(trees, inputs):
    def walk(node, row):
        while isinstance(node, dict):
            node = node['left'] if row[node['feature']] <= node['threshold'] else node['right']
        return node
    return np.array([sum(walk(tree, row) for tree in trees) for row in inputs])


def _fit_trees(panels, static, means, targets):
    import lightgbm as lgb

    inputs = np.column_stack((panels.reshape(len(static), -1), static))
    dataset = lgb.Dataset(inputs, label=targets, feature_name=TREE_NAMES)
    def objective(raw, dataset):
        mu = means * np.exp(raw)
        return mu - dataset.get_label() + RIDGE * raw, mu + RIDGE
    params = dict(TREE_CONFIG, objective=objective, num_threads=1, seed=SEED, deterministic=True,
                  feature_pre_filter=False, force_col_wise=True, boost_from_average=False, verbosity=-1)
    rounds = params.pop('rounds')
    booster = lgb.train(params, dataset, num_boost_round=rounds)
    trees = [_compact_tree(tree['tree_structure']) for tree in booster.dump_model()['tree_info']]
    np.testing.assert_allclose(_tree_forward(trees, inputs), booster.predict(inputs, raw_score=True),
                               atol=1e-10, rtol=1e-10)
    return {'trees': trees, 'feature_gain': dict(zip(TREE_NAMES, booster.feature_importance('gain').tolist())),
            'lightgbm_training_version': lgb.__version__}


def fit(rows, *, cutoff_at):
    """Fit once on strictly earlier days; actual freeze/result chronology is checked when supplied."""
    cutoff = aware(cutoff_at)
    panels, static, means, targets, signatures = [], [], [], [], []
    used = set()
    for row in sorted(rows, key=lambda x: x['match_id']):
        day = date.fromisoformat(row['game_date'])
        if day >= cutoff.date():
            continue
        if any(k in row for k in ('cutoff_at', 'kickoff_at', 'observed_at')):
            feature_time, kickoff, observed = (aware(row[k]) for k in ('cutoff_at', 'kickoff_at', 'observed_at'))
            if not feature_time < kickoff < observed or day != kickoff.date():
                raise ValueError('invalid original result or feature chronology')
            if observed > cutoff:
                continue
        mid = row['match_id']
        if mid in used:
            raise ValueError('duplicate training match: ' + mid)
        used.add(mid)
        matrix = _matrix(row['baseline_matrix'])
        x, c, mu = _features(row['sequence'], matrix)
        goals = [row['home_goals'], row['away_goals']]
        if any(type(v) is not int or v < 0 for v in goals):
            raise ValueError('invalid regulation score')
        panels.extend(x)
        static.extend(c)
        means.extend(mu)
        targets.extend(goals)
        signatures.append([mid, row['game_date'], goals, np.asarray(row['sequence']).tolist(),
                           hashlib.sha256(np.asarray(matrix, dtype='<f8').tobytes()).hexdigest()])
    if not used:
        raise ValueError('no training fixtures before cutoff')
    tensors = [np.asarray(values, dtype=float) for values in (panels, static, means, targets)]
    return {'version': VERSION, 'cutoff_at': cutoff_at, 'training_matches': len(used),
            'sample_sha256': digest(signatures), 'step_features': list(STEP_NAMES),
            'static_features': list(STATIC_NAMES), 'tree_features': TREE_NAMES,
            'feature_encoding': 'both sides oldest to newest, left zero-pad, raw GF/GA divided by 3',
            'seed': SEED, 'objective': 'baseline_mean*exp(delta) - goals*delta + ridge*delta^2/2',
            'ridge': RIDGE, 'max_abs_tilt': MAX_TILT, 'lstm_config': LSTM_CONFIG, 'lightgbm_config': TREE_CONFIG,
            'lstm': _fit_lstm(*tensors), 'lightgbm': _fit_trees(*tensors), 'active_model_changed': False}


def apply(artifact, baseline_matrix, sequence):
    """Apply both fitted natural-parameter residuals after market calibration, preserving score support."""
    if artifact['version'] != VERSION:
        raise ValueError('unexpected sequence artifact version')
    m = _matrix(baseline_matrix)
    panels, static, _ = _features(sequence, m)
    inputs = np.column_stack((panels.reshape(2, -1), static))
    deltas = {'lstm': _lstm_forward(artifact['lstm'], panels, static),
              'lightgbm': _tree_forward(artifact['lightgbm']['trees'], inputs)}
    result = {'baseline': m.copy()}
    home, away = np.indices(m.shape)
    for name, delta in deltas.items():
        dh, da = np.clip(delta, -artifact['max_abs_tilt'], artifact['max_abs_tilt'])
        if dh == 0 and da == 0:
            result[name] = m.copy()
            continue
        tilt = dh * home + da * away
        adjusted = m * np.exp(tilt - tilt[m > 0].max())
        result[name] = adjusted / adjusted.sum()
    return result
