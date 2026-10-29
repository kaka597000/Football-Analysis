"""Append-only historical replay receipts. This is not prematch freezing."""
import json
from pathlib import Path
import numpy as np
from schema import digest, file_hash, save, now

def check_matrix(matrix):
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or m.shape[0] != m.shape[1] or not np.isfinite(m).all() or (m < 0).any():
        raise ValueError('invalid score matrix')
    if abs(float(m.sum()) - 1.) > 1e-10:
        raise ValueError('matrix is not normalized')
    return m

def record(root, prediction):
    prediction = dict(prediction)
    if prediction.get('status') == 'PREDICTED':
        prediction['matrix'] = check_matrix(prediction['matrix']).tolist()
        prediction['matrix_sha256'] = digest(prediction['matrix'])
    prediction.update(record_type='HISTORICAL_REPLAY', actual_generated_at=now(), formal_kpi_eligible=False)
    key = digest([prediction['match_id'], prediction['model'], prediction['fold']])
    p = Path(root) / (key + '.json')
    save(p, prediction)
    receipt = {'path': str(p), 'sha256': file_hash(p), 'match_id': prediction['match_id'], 'model': prediction['model']}
    save(p.with_suffix('.receipt.json'), receipt)
    return receipt

def read(receipt):
    p = Path(receipt['path'])
    if not p.exists() or file_hash(p) != receipt['sha256']:
        raise ValueError('prediction receipt mismatch')
    row = json.loads(p.read_text())
    if row['match_id'] != receipt['match_id'] or row['model'] != receipt['model']:
        raise ValueError('receipt identity mismatch')
    if row['status'] == 'PREDICTED':
        check_matrix(row['matrix'])
        if digest(row['matrix']) != row['matrix_sha256']:
            raise ValueError('matrix hash mismatch')
    return row
