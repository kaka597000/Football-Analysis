"""Independent Europa/Conference research pairs. Never edits active routes."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
from automation.trainer import _engine


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def aware(s):
    d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('timezone required')
    return d.astimezone(timezone.utc)


def source_hashes():
    root = Path(__file__).resolve().parent
    names = ('europe_pair_adapter.py', 'automation/trainer.py', 'optimization/models.py',
             'optimization/schema.py', 'optimization/features.py', 'nations_league/safe_cli.py',
             'continental/build_europe_pairs.py')
    return {name: sha(root / name) for name in names}


def predict_pair(fixture, route):
    from nations_league.safe_cli import TARGET_LABELS
    labels = {name.replace('_', '') for name in TARGET_LABELS}
    if any(str(k).lower().replace('-', '').replace('_', '') in labels for k in fixture):
        raise ValueError('target contains result or postmatch labels')
    path = Path(route['index_path'])
    expected_index = route['index_sha256']
    index_bytes = path.read_bytes()
    if hashlib.sha256(index_bytes).hexdigest() != expected_index:
        raise ValueError('research index checksum mismatch')
    index = json.loads(index_bytes)
    retained_hashes = {path: expected_index}
    comp = index['competition']
    if index.get('source_files_sha256') != source_hashes():
        raise ValueError('research engine source changed')
    rows_path = path.parent / 'verified-rows.json'
    retained_hashes[rows_path] = index['rows_file_sha256']
    if sha(rows_path) != index['rows_file_sha256']:
        raise ValueError('retained training rows checksum mismatch')
    if (comp not in ('uefa.europa', 'uefa.europa.conf')
            or fixture.get('competition') != comp or fixture.get('cohort') != comp
            or fixture.get('stage') != 'league-phase' or fixture.get('phase') != 'PreMatch'
            or fixture.get('gender') not in ('men', 'male') or fixture.get('age') != 'senior'
            or fixture.get('score_basis') != 'regulation_90'):
        raise ValueError('research pair scope mismatch')
    cutoff, kickoff = aware(fixture['cutoff_at']), aware(fixture['kickoff_at'])
    if not aware(index['created_at']) <= cutoff <= datetime.now(timezone.utc) < kickoff:
        raise ValueError('artifact must predate genuine prematch cutoff')
    if fixture.get('date') != kickoff.date().isoformat() or fixture['date'] <= index['fit_end']:
        raise ValueError('target must follow training history')
    if fixture.get('neutral') not in (True, False) or type(fixture.get('neutral')) is not bool:
        raise ValueError('verified venue required')
    if fixture['home'] == fixture['away'] or any(fixture[k] not in index['teams'] for k in ('home', 'away')):
        raise ValueError('unseen or duplicate team identity')
    engine = _engine(); result = {}
    for role in ('current', 'test'):
        entry = index[role]; ap = path.parent / entry['file']
        if ap.resolve().parent != path.parent.resolve():
            raise ValueError('research artifact outside retained directory')
        artifact_bytes = ap.read_bytes()
        if hashlib.sha256(artifact_bytes).hexdigest() != entry['sha256']:
            raise ValueError('research artifact checksum mismatch')
        retained_hashes[ap] = entry['sha256']
        artifact = json.loads(artifact_bytes)
        if (artifact['competition'] != comp or artifact['fit_end'] != index['fit_end']
                or artifact.get('role') != role or artifact.get('schema') != 'WCPP-EUROPE-PAIR-ARTIFACT-1'):
            raise ValueError('artifact identity mismatch')
        core = artifact['core']
        mu = np.asarray(core['constant']) if role == 'current' else engine.rates(core, [fixture])[0]
        matrix = engine.matrix(mu)
        result[role + '_model'] = {'version': artifact['version'], 'matrix': matrix.tolist(),
            'primary': '-'.join(map(str, np.unravel_index(matrix.argmax(), matrix.shape))),
            'expected_goals': list(map(float, mu)), 'training_data_cutoff': index['fit_end'],
            'status': 'PREDICTED_RESEARCH_ONLY', 'artifact_sha256': entry['sha256'],
            'formal_kpi_eligible': False, 'automatic_promotion': False}
    # Fail closed if a retained input/code changes or kickoff passes during work.
    if any(sha(p) != expected for p, expected in retained_hashes.items()):
        raise ValueError('research inputs changed during computation')
    if source_hashes() != index['source_files_sha256']:
        raise ValueError('research engine source changed during computation')
    if not aware(index['created_at']) <= cutoff <= datetime.now(timezone.utc) < kickoff:
        raise ValueError('prematch clock expired during computation')
    result.update(status='READY_RESEARCH_PAIR', competition=comp,
        audit={'index_sha256': route['index_sha256'], 'source_rows_sha256': index['source_rows_sha256'],
               'historical_asof_certified': False, 'active_registry_modified': False,
               'source_files_sha256': index['source_files_sha256'],
               'inputs_rechecked_after_computation': True,
               'fixture_identity': {k: fixture[k] for k in ('match_id','home','away','kickoff_at','cutoff_at','phase')}},
        automatic_promotion=False, research_only=True)
    return result
