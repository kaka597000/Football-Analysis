"""Reconcile already archived 5Dollar statistics to audited history. No guessed IDs."""
import copy
import json
import re
from pathlib import Path


def enrich(rows, archive_root='/workspace/WCPP/reports'):
    root = Path(archive_root).resolve()
    output = copy.deepcopy(rows); cache = {}; count = 0; missing = 0
    for r in output:
        evidence = r.get('api_evidence', {})
        source = evidence.get('source_path'); source_key = evidence.get('source_key','')
        if not source or not re.fullmatch(r'5dollar:\d+',source_key):
            missing += 1; continue
        path = Path(source).resolve()
        if not path.is_relative_to(root): raise ValueError('archive path outside approved source root')
        if path not in cache:
            if not path.is_file(): cache[path] = {}; missing += 1; continue
            document = json.loads(path.read_text())
            response = document.get('response',document)
            cache[path] = {str(x['id']):x for x in response.get('data',[]) if isinstance(x,dict) and x.get('id')}
        raw = cache[path].get(source_key.split(':')[1])
        if not raw: missing += 1; continue
        scores = raw.get('goals',{})
        if (scores.get('home'),scores.get('away')) != (r['home_goals'],r['away_goals']):
            raise ValueError('provider/archive score identity conflict')
        if raw.get('kickoff_utc','')[:10] != r['date']:
            raise ValueError('provider/archive date conflict')
        stats = raw.get('statistics') or {}
        values = {side:{k:(stats.get(k) or {}).get(side) for k in ('shots_on_target','shots_off_target','possession')}
                  for side in ('home','away')}
        if any(type(v) not in (int,float) or not 0 <= v <= 100 for d in values.values() for v in d.values()):
            missing += 1; continue
        r['process'] = values
        r['process_evidence'] = {'source_url':r['source_url'],'query_time':evidence['query_time'],
                                 'source_key':source_key,'mapping':'existing_audited_provider_ID_plus_date_and_score',
                                 'first_known_at':None,'source_path':str(path)}
        count += 1
    return output, {'matched_process_rows':count,'missing_process_rows':missing,
                    'total_rows':len(rows),'historical_first_known_timestamps_available':False}
