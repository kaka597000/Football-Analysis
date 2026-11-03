"""Historical feature availability is separate from collection or match dates."""
from datetime import datetime

def timestamp(value):
    if not isinstance(value,str):raise ValueError('explicit as-of timestamp required')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('timezone required')
    return result

def audit_feature_availability(rows):
    missing=0;late=0;checked=0
    for row in rows:
        for key in ('xg_evidence','process_evidence'):
            evidence=row.get(key)
            if not evidence:continue
            checked+=1
            known=evidence.get('first_known_at')
            boundary=row.get('feature_available_by')
            if not known or not boundary:
                missing+=1;continue
            if timestamp(known)>timestamp(boundary):late+=1
    return {'schema':'WCPP-ASOF-FEATURE-CONTRACT-1','feature_blocks':checked,
            'missing_first_known_or_boundary':missing,'late_blocks':late,
            'status':'CERTIFIED_EXPLICIT_AVAILABILITY' if checked and not missing and not late else 'NOT_CERTIFIED',
            'collection_time_is_first_known':False}

def require_historical_replay(rows):
    audit=audit_feature_availability(rows)
    if audit['status']!='CERTIFIED_EXPLICIT_AVAILABILITY':
        raise ValueError('historical feature availability not certified')
    return audit
