"""Research-only score-distribution calibration. Does not change comprehensive Primary."""
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.optimize import minimize

VERSION = 'WCPP-SCORE-CALIBRATION-0.1-RESEARCH'
BASE = 'M2-h365-r30'
FEATURES = ['home_goals', 'away_goals', 'home_zero', 'away_zero', 'total_ge4']

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def validate_matrix(matrix):
    m = np.array(matrix, dtype=float, copy=True)
    if m.ndim != 2 or m.shape[0] != m.shape[1] or m.shape[0] < 2 or not np.isfinite(m).all() or np.any(m < 0):
        raise ValueError('invalid score matrix')
    if abs(float(m.sum()) - 1) > 1e-10:
        raise ValueError('score matrix must sum to one')
    return m

def features(size):
    h, a = np.indices((size, size))
    return np.stack([h, a, h == 0, a == 0, h+a >= 4], axis=-1).astype(float).reshape(-1, 5)

def check_identity(identity, binding):
    expected = {'model': BASE, 'feature_group': 'core', 'half_life': 365, 'ridge': 30, 'alpha': 0.0}
    if not isinstance(identity, dict) or any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError('unknown or incompatible base model: requires fixed systematic M2-h365-r30 core')
    if identity.get('cohort') not in binding['cohorts'] or identity.get('source_protocol_sha256') not in binding['source_protocol_sha256']:
        raise ValueError('base cohort/protocol identity is not bound to calibration')

def validate_artifact(artifact):
    if artifact.get('version') != VERSION or artifact.get('candidate') not in ('C0', 'C1', 'C2'):
        raise ValueError('unknown calibration artifact')
    if artifact.get('artifact_sha256') != digest({k:v for k,v in artifact.items() if k != 'artifact_sha256'}):
        raise ValueError('calibration artifact hash mismatch')
    theta = np.asarray(artifact.get('theta'), dtype=float)
    if theta.shape != (5,) or not np.isfinite(theta).all():
        raise ValueError('invalid calibration coefficients')
    if artifact.get('base_binding', {}).get('model') != BASE or artifact.get('promotion') != 'DISABLED_RESEARCH_ONLY':
        raise ValueError('invalid calibration model binding')
    return artifact

def load(path):
    return validate_artifact(json.loads(Path(path).read_text()))

def save(artifact, path):
    value = {k:v for k,v in artifact.items() if k != 'artifact_sha256'}
    value['artifact_sha256'] = digest(value)
    validate_artifact(value)
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        raise FileExistsError(f'append-only artifact exists: {p}')
    p.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    return value

def apply(artifact, matrix, *, base_identity):
    validate_artifact(artifact)
    check_identity(base_identity, artifact['base_binding'])
    m = validate_matrix(matrix)
    if artifact['candidate'] == 'C0':
        return m
    log_tilt = features(len(m)) @ np.asarray(artifact['theta'])
    support = m.reshape(-1) > 0
    # Shift over supported cells only; preserve original zero probabilities.
    logp = np.full(m.size, -np.inf)
    logp[support] = np.log(m.reshape(-1)[support]) + log_tilt[support]
    q = np.exp(logp - np.max(logp)); q /= q.sum()
    return q.reshape(m.shape)

def train(matrices, labels, *, candidate, base_binding, training_metadata=None):
    """Fit only supplied training rows; caller owns chronological split and targets."""
    validated = [validate_matrix(m) for m in matrices]
    if not validated:
        raise ValueError('empty training matrices')
    size = max(len(m) for m in validated)
    # Different tail truncations are zero-padded without inventing support.
    ms = np.stack([np.pad(m, ((0, size-len(m)), (0, size-len(m)))) for m in validated])
    y = np.asarray(labels)
    if y.shape != (len(ms), 2) or len(ms) == 0 or not np.isfinite(y).all() or np.any(y < 0) or np.any(y != np.floor(y)):
        raise ValueError('invalid training target scores')
    y = y.astype(int)
    if np.any(y >= ms.shape[1]):
        raise ValueError('target outside declared matrix support')
    f = features(ms.shape[1]); p = ms.reshape(len(ms), -1)
    target_indices = y[:,0]*ms.shape[1]+y[:,1]
    if np.any(p[np.arange(len(p)), target_indices] <= 0):
        raise ValueError('observed score has zero base support')
    theta = np.zeros(5); optimizer = None
    if candidate == 'C1':
        expected = p @ f[:,:2]
        scales = np.clip(y.sum(axis=0)/expected.sum(axis=0), 2/3, 1.5)
        theta[:2] = np.log(scales)
    elif candidate == 'C2':
        target_mean = f[target_indices].mean(axis=0)
        def objective(t):
            log_tilt = f @ t
            shift = log_tilt.max()
            tilted = np.exp(log_tilt-shift)
            z = p @ tilted
            loss = np.mean(np.log(z)+shift) - target_mean @ t + .01*np.sum(t*t)
            gradient = ((p @ (tilted[:,None]*f))/z[:,None]).mean(axis=0)-target_mean+.02*t
            return float(loss), gradient
        bound = float(np.log(1.5))
        result = minimize(objective, theta, jac=True, method='L-BFGS-B', bounds=[(-bound,bound)]*2+[(-1,1)]*3, options={'maxiter':200,'ftol':1e-12,'gtol':1e-7})
        if not result.success:
            raise RuntimeError(f'calibration optimization failed: {result.message}')
        theta = result.x
        optimizer = {'success': bool(result.success), 'iterations': int(result.nit), 'message': str(result.message), 'objective_without_base_constant': float(result.fun)}
    elif candidate != 'C0':
        raise ValueError('unknown candidate')
    value = {'version': VERSION, 'candidate':candidate, 'theta':theta.tolist(), 'features':FEATURES,
             'base_binding':base_binding, 'promotion':'DISABLED_RESEARCH_ONLY', 'training_n':len(ms),
             'training_metadata':training_metadata or {}, 'optimizer':optimizer,
             'limitations':['Previously inspected history; not future frozen performance','Statistical matrix mode is not comprehensive Primary','No current/test routing or parameter changes']}
    value['artifact_sha256'] = digest(value)
    return validate_artifact(value)

def date_split(rows):
    """Absolute calendar split across all cohorts; no day enters two segments."""
    dates = sorted({r['date'][:10] for r in rows})
    if len(dates) < 5:
        raise ValueError('insufficient distinct calendar days')
    i, j = int(.6*len(dates)), int(.8*len(dates))
    groups = {'train':dates[:i], 'select':dates[i:j], 'final':dates[j:]}
    lookup = {day:key for key, days in groups.items() for day in days}
    return {key:[i for i,r in enumerate(rows) if lookup[r['date'][:10]] == key] for key in groups}, groups
