"""Fixed five-match Transformer adjustment to the common market-calibrated score matrix."""
from datetime import date
import hashlib

import numpy as np
from scipy.special import softmax

from .elo_kelly_shadow import aware, digest
from .sequence_models import MAX_TILT, RIDGE, SEED, STATIC_NAMES, STEP_NAMES, WINDOW, _features
from .three_arm_model import _matrix

VERSION = 'WCPP-TRANSFORMER-SCORE-1'
CONFIG = {'d_model': 16, 'heads': 2, 'feedforward': 32, 'layers': 1, 'pre_norm': True,
          'activation': 'relu', 'dropout': 0., 'layer_norm_eps': 1e-5, 'position_init_std': .02,
          'epochs': 60, 'learning_rate': .003, 'weight_decay': .01, 'optimizer': 'Adam'}


def _forward(weights, panels, context):
    """Reproduce one pre-norm PyTorch encoder layer with portable NumPy operations."""
    w = {key: np.asarray(value, dtype=np.float64) for key, value in weights.items()}
    heads = CONFIG['heads']
    width = CONFIG['d_model']
    depth = width // heads
    batch, length, _ = panels.shape

    def linear(x, name):
        return x @ w[name + '.weight'].T + w[name + '.bias']

    def normalize(x, name):
        centered = x - x.mean(axis=-1, keepdims=True)
        variance = (centered ** 2).mean(axis=-1, keepdims=True)
        return centered / np.sqrt(variance + CONFIG['layer_norm_eps']) * w[name + '.weight'] + w[name + '.bias']

    valid = (panels[:, :, 3] != 0) | (panels[:, :, 7] != 0)
    present = valid.any(axis=1)
    # All-empty histories get zero pooled features. A dummy unmasked key keeps their softmax finite.
    valid[~present, -1] = True
    x = linear(panels, 'input_projection') + w['positions']
    z = normalize(x, 'encoder.norm1')
    qkv = z @ w['encoder.self_attn.in_proj_weight'].T + w['encoder.self_attn.in_proj_bias']
    q, k, v = (a.reshape(batch, length, heads, depth).transpose(0, 2, 1, 3)
               for a in np.split(qkv, 3, axis=-1))
    attention = (q @ k.swapaxes(2, 3)) / np.sqrt(depth)
    probabilities = softmax(np.where(valid[:, None, None, :], attention, -np.inf), axis=-1)
    attended = (probabilities @ v).transpose(0, 2, 1, 3).reshape(batch, length, width)
    x = x + linear(attended, 'encoder.self_attn.out_proj')
    hidden = np.maximum(0., linear(normalize(x, 'encoder.norm2'), 'encoder.linear1'))
    x = x + linear(hidden, 'encoder.linear2')
    # Source histories are left-padded, so the last token is valid for every nonempty row.
    pooled = np.where(present[:, None], x[:, -1], 0.)
    return linear(np.column_stack((pooled, context)), 'readout')[:, 0]


def _train(panels, context, means, targets):
    import torch
    from torch import nn

    width = CONFIG['d_model']
    dtype = torch.float64

    class ScoreEncoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.input_projection = nn.Linear(len(STEP_NAMES), width, dtype=dtype)
            self.positions = nn.Parameter(torch.empty(WINDOW, width, dtype=dtype))
            nn.init.normal_(self.positions, std=CONFIG['position_init_std'])
            self.encoder = nn.TransformerEncoderLayer(
                d_model=width, nhead=CONFIG['heads'], dim_feedforward=CONFIG['feedforward'],
                activation=CONFIG['activation'], dropout=CONFIG['dropout'], batch_first=True,
                norm_first=CONFIG['pre_norm'], layer_norm_eps=CONFIG['layer_norm_eps'], dtype=dtype)
            self.readout = nn.Linear(width + len(STATIC_NAMES), 1, dtype=dtype)
            nn.init.zeros_(self.readout.weight)
            nn.init.zeros_(self.readout.bias)

        def forward(self, x, static):
            valid = (x[:, :, 3] != 0) | (x[:, :, 7] != 0)
            present = valid.any(dim=1)
            valid[~present, -1] = True
            states = self.encoder(self.input_projection(x) + self.positions, src_key_padding_mask=~valid)
            pooled = torch.where(present[:, None], states[:, -1], 0.)
            return self.readout(torch.cat((pooled, static), dim=1)).squeeze(1)

    x, static, prior, y = (torch.from_numpy(a) for a in (panels, context, means, targets))
    previous_threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(SEED)
            network = ScoreEncoder()
            optimizer = torch.optim.Adam(network.parameters(), lr=CONFIG['learning_rate'],
                                         weight_decay=CONFIG['weight_decay'])
            for _ in range(CONFIG['epochs']):
                optimizer.zero_grad()
                delta = network(x, static)
                loss = (prior * delta.exp() - y * delta + RIDGE * delta.square() / 2).mean()
                loss.backward()
                optimizer.step()
            with torch.no_grad():
                native = network(x, static).numpy()
                weights = {name: value.tolist() for name, value in network.state_dict().items()}
    finally:
        torch.set_num_threads(previous_threads)
    restored = _forward(weights, panels, context)
    np.testing.assert_allclose(restored, native, rtol=1e-10, atol=1e-10)
    return weights, str(torch.__version__), float(np.max(np.abs(restored - native)))


def fit(rows, *, cutoff_at):
    """Use the same match sort, strict date boundary and sample fingerprint as the LSTM study."""
    cutoff = aware(cutoff_at)
    panels, context, means, targets = [], [], [], []
    signatures = []
    used = set()
    for row in sorted(rows, key=lambda r: r['match_id']):
        day = date.fromisoformat(row['game_date'])
        if day >= cutoff.date():
            continue
        if any(key in row for key in ('cutoff_at', 'kickoff_at', 'observed_at')):
            feature_at, kickoff, observed = (aware(row[key]) for key in ('cutoff_at', 'kickoff_at', 'observed_at'))
            if not feature_at < kickoff < observed or day != kickoff.date():
                raise ValueError('invalid original result or feature chronology')
            if observed > cutoff:
                continue
        mid = row['match_id']
        if mid in used:
            raise ValueError('duplicate training match: ' + mid)
        used.add(mid)
        matrix = _matrix(row['baseline_matrix'])
        x, static, mu = _features(row['sequence'], matrix)
        labels = [row['home_goals'], row['away_goals']]
        if any(type(v) is not int or v < 0 for v in labels):
            raise ValueError('invalid regulation score')
        panels.extend(x)
        context.extend(static)
        means.extend(mu)
        targets.extend(labels)
        signatures.append([mid, row['game_date'], labels, np.asarray(row['sequence']).tolist(),
                           hashlib.sha256(np.asarray(matrix, dtype='<f8').tobytes()).hexdigest()])
    if not used:
        raise ValueError('no training fixtures before cutoff')
    weights, torch_version, error = _train(*(np.asarray(a, dtype=float) for a in (panels, context, means, targets)))
    return {'version': VERSION, 'cutoff_at': cutoff_at, 'training_matches': len(used),
            'sample_sha256': digest(signatures), 'step_features': list(STEP_NAMES),
            'static_features': list(STATIC_NAMES), 'window': WINDOW,
            'mask_policy': 'Keys where either team has a real prior match; empty histories produce zero pooled features.',
            'seed': SEED, 'objective': 'baseline_mean*exp(delta) - goals*delta + ridge*delta^2/2',
            'ridge': RIDGE, 'max_abs_tilt': MAX_TILT, 'config': dict(CONFIG), 'weights': weights,
            'torch_training_version': torch_version, 'max_native_replay_error': error,
            'parameter_count': sum(np.size(v) for v in weights.values()), 'active_model_changed': False}


def residuals(artifact, baseline_matrix, sequence):
    """Return the unclipped home and away log-rate changes."""
    if artifact['version'] != VERSION:
        raise ValueError('unexpected Transformer artifact version')
    panels, context, _ = _features(sequence, _matrix(baseline_matrix))
    return _forward(artifact['weights'], panels, context)


def apply(artifact, baseline_matrix, sequence):
    """Exponentially tilt the original score probabilities using the fitted rate changes."""
    m = _matrix(baseline_matrix)
    dh, da = np.clip(residuals(artifact, m, sequence), -artifact['max_abs_tilt'], artifact['max_abs_tilt'])
    if dh == 0 and da == 0:
        return m.copy()
    home, away = np.indices(m.shape)
    tilt = dh * home + da * away
    adjusted = m * np.exp(tilt - tilt[m > 0].max())
    return adjusted / adjusted.sum()
