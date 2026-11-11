"""Inspect preserved review archives without certifying retrospective holdouts."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

try:
    from .analysis_freeze import aware, verify
    from .score_concentration import diagnose, _group
except ImportError:
    from analysis_freeze import aware, verify
    from score_concentration import diagnose, _group


def inspect_archive_records(batch_records, results, *, batch, earliest=None):
    entries=[r for r in batch_records if r['batch']==batch]
    if not entries:
        raise ValueError('requested preserved batch not found')
    mapping={r['pair']['match_id']:str(r['fotmob_match_id']) for r in entries}
    if len(mapping)!=len(entries) or len(set(mapping.values()))!=len(entries):
        raise ValueError('duplicate match in selected batch')
    actual={str(r['fotmob_match_id']):r for r in results['matches']}
    if len(actual)!=len(results['matches']):
        raise ValueError('duplicate archived result')
    records=[r['pair'] for r in entries] if earliest is None else earliest
    if len(records)!=len(mapping) or {r['match_id'] for r in records}!=set(mapping):
        raise ValueError('earliest and selected batch must have identical match populations')
    latest={r['pair']['match_id']:r['pair'] for r in entries}
    rows=[]; conflicts=[]
    for rec in records:
        verify(rec)
        fixture=rec['fixture']; mid=rec['match_id']
        if (fixture['match_id']!=mid or rec['track']!='current-test-pair' or rec['stage']!='prematch'
                or not aware(fixture['cutoff_at'])<=aware(rec['recorded_at'])<aware(fixture['kickoff_at'])):
            raise ValueError('preserved prematch pair identity/chronology invalid')
        if earliest is not None and (rec['revision']!=1 or rec['previous_sha256'] is not None or rec['evaluation_slot']!='FIRST_PREMATCH'):
            raise ValueError('earliest analysis requires first prematch records only')
        result=actual[mapping[mid]]
        if (result.get('status')!='FT_90_MIN' or result.get('identity_verified') is not True
                or result['source'].get('fotmob_score_match_espn') is not True):
            raise ValueError('archived verified 90-minute result required')
        conflict=aware(fixture['kickoff_at'])!=aware(latest[mid]['fixture']['kickoff_at'])
        if conflict:
            conflicts.append(mid)
        for track in ('current_model','test_model'):
            pred=rec['payload'][track]
            if pred.get('input',fixture)!=fixture:
                raise ValueError('prediction fixture differs from freeze')
            if conflict and (pred.get('matrix') is not None or pred.get('primary') is not None):
                raise ValueError('scored forecast has unresolved kickoff conflict')
            rows.append({'match_id':mid,'track':track,'cohort':fixture['cohort'],
                'prediction':pred,'result':{'home_goals':result['home_goals_90'],'away_goals':result['away_goals_90']},
                'source':{'freeze_id':rec['freeze_id'],'record_sha256':rec['record_sha256'],
                    'evaluation_slot':rec['evaluation_slot'],'recorded_at':rec['recorded_at'],
                    'result_source':result['source']['source_url'],'result_response_sha256':result['source']['sha256'],
                    'unexecuted_record_kickoff_conflict':conflict}})
    report=diagnose(rows)
    report['archived_batch']=batch
    report['population_kind']='RECOVERED_FIRST_PREMATCH' if earliest is not None else 'PRESERVED_ISSUED_BATCH_MAY_INCLUDE_REVISIONS'
    report['freeze_slots']=dict(Counter(r['evaluation_slot'] for r in records))
    report['unexecuted_kickoff_conflicts']=conflicts
    groups=defaultdict(list)
    for row in rows:
        groups[(row['track'],row['prediction'].get('version') or 'UNAVAILABLE')].append(row)
    report['mixed_cohort_descriptive_overviews']=[{'track':k[0],'version':k[1],**_group(v)} for k,v in sorted(groups.items())]
    report['limitations'] += ['Mixed-cohort overviews are descriptive only and never replace cohort/version results.',
        'Archived identity flags are not an independent fresh result query; verify original source bytes and archive manifest separately.',
        'Recovered first versions do not prove an exhaustive search of all historical storage.']
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('frozen_records'); p.add_argument('results'); p.add_argument('--batch',required=True)
    p.add_argument('--earliest-pairs'); p.add_argument('--out',required=True)
    a=p.parse_args()
    paths=[Path(a.frozen_records),Path(a.results)]
    if a.earliest_pairs:
        paths.append(Path(a.earliest_pairs))
    data=[json.loads(p.read_text()) for p in paths]
    report=inspect_archive_records(data[0],data[1],batch=a.batch,earliest=data[2] if len(data)>2 else None)
    report['input_file_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    with Path(a.out).open('x',encoding='utf-8') as f:
        json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)


if __name__=='__main__': main()
