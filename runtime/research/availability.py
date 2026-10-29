"""Normalize provider availability without treating omission as health clearance.

This is evidence extraction, not a trained injury adjustment or official status.
"""

def _name(person):
    return str(person.get('name') or ' '.join(str(person.get(k) or '') for k in ['firstName','lastName'])).strip()

def _identity(person):
    return ('id',str(person['id'])) if person.get('id') is not None else ('name',_name(person).casefold())

def extract(details,*,coach_ids=()):
    lineup=(details.get('content') or {}).get('lineup') or {}
    result={'source':'FOTMOB_PROVIDER_REPORT','official_confirmation':False,'sides':{}}
    for side in ['home','away']:
        team=lineup.get(side+'Team') or {}; coach=team.get('coach') or {}
        coaches=coach if isinstance(coach,list) else [coach]
        coach_keys={_identity(p) for p in coaches if isinstance(p,dict) and (p.get('id') is not None or _name(p))}
        coach_keys.update(('id',str(v)) for v in coach_ids)
        rows=[];seen=set();fields=[]
        for field in ['unavailable','injuredPlayers','suspendedPlayers','injuries','suspensions']:
            values=team.get(field)
            if isinstance(values,list):fields.append(field)
            for person in values or []:
                if not isinstance(person,dict):continue
                info=person.get('unavailability') or {}
                kind=info.get('type') or {'injuredPlayers':'injury','injuries':'injury','suspendedPlayers':'suspension','suspensions':'suspension'}.get(field,'unknown')
                key=(_identity(person),kind)
                if key in seen:continue
                seen.add(key)
                rows.append({'person_id':person.get('id'),'name':_name(person),'provider_type':kind,
                             'role':'coach' if _identity(person) in coach_keys else 'unverified_person_role',
                             'provider_position_id':person.get('positionId'),
                             'expected_return':info.get('expectedReturn'),'expected_return_date':info.get('expectedReturnDate'),
                             'source_field':field,'official_confirmation':False})
        result['sides'][side]={'status':'PROVIDER_REPORTED' if rows else 'PROVIDER_EMPTY_UNKNOWN' if fields else 'SCHEMA_MISSING_UNKNOWN',
                               'observed_fields':fields,'items':rows,'health_clearance':False}
    return result

def compare(current,previous,*,coach_ids=()):
    def keys(value):
        return {(side,str(p['person_id']) if p['person_id'] is not None else p['name'].casefold(),p['provider_type'])
                for side,v in value['sides'].items() for p in v['items']}
    now,old=keys(extract(current,coach_ids=coach_ids)),keys(extract(previous,coach_ids=coach_ids))
    return {'new_provider_listings':[list(x) for x in sorted(now-old)],
            'removed_provider_listings':[list(x) for x in sorted(old-now)],
            'interpretation':'LIST_CHANGE_ONLY_NOT_INJURY_ONSET_RECOVERY_OR_OFFICIAL_CONFIRMATION'}

def main():
    import argparse
    import hashlib
    import json
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input');parser.add_argument('--previous');parser.add_argument('--out',required=True)
    parser.add_argument('--coach-id',action='append',default=[],help='Independently verified staff ID; never infer from suspension alone')
    args=parser.parse_args();src=Path(args.input);data=json.loads(src.read_text())
    result={'schema':'WCPP-AVAILABILITY-EVIDENCE-1','source_path':str(src.resolve()),
            'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'availability':extract(data,coach_ids=args.coach_id),
            'numerical_forecast_changed':False}
    if args.previous:
        old=Path(args.previous)
        result.update(previous_path=str(old.resolve()),previous_sha256=hashlib.sha256(old.read_bytes()).hexdigest(),
                      provider_list_delta=compare(data,json.loads(old.read_text()),coach_ids=args.coach_id))
    with Path(args.out).open('x') as handle:json.dump(result,handle,ensure_ascii=False,indent=2,allow_nan=False)

if __name__=='__main__':main()
