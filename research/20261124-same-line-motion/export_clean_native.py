"""Rebuild first-freeze, input-only market rows from the pinned Data Git object."""
import argparse
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

DATA_REV = 'a6d45024cf6cf622f05e4b378ab53cfe2d6ae329'
PACKS = {
    '20261107-night19-55matches': '07c79bc107db14c8113cadab6ed6194bbe946f82cdbea9e2ea61c0a30bd237ac',
    '20261108-night19-46matches': '853e649f72b2cab552b37550ad00c5f3526226b55d9458c62894e3ac2a1eb123',
    '20261108-big5-remaining-22matches': '0b05a98d5a62d7c0aa6e86d4c6dc0900681442723440df9b721b82111838cb3a',
}
SCOPE = {'eng.1','esp.1','ger.1','ita.1','fra.1','jpn.1','kor.1','aus.1','nor.1','swe.1','ned.1','usa.1',
         'uefa.champions','uefa.europa','uefa.europa.conf','afc.champions','fifa.world','uefa.nations','jpn.league_cup','bra.1'}
SPLIT = datetime(2026, 11, 8, 4, tzinfo=timezone.utc)

def digest(b): return hashlib.sha256(b).hexdigest()
def aware(s):
    x = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    if x.tzinfo is None: raise ValueError('timezone required')
    return x.astimezone(timezone.utc)
def stamp(s): return aware(s).isoformat().replace('+00:00', 'Z') if s else None
def git_show(repo, path):
    return subprocess.check_output(['git','-C',str(repo),'show',f'{DATA_REV}:{path}'])
def helpers(source):
    runtime = str(source/'runtime')
    research = str(source/'research'/'20261124-same-line-motion')
    sys.path[:0] = [runtime, research]
    from research.analysis_freeze import verify
    from early_boards import project_quote
    return verify, project_quote

def archive(repo, batch):
    base = f'workspace-updates/{batch}/'
    manifest = json.loads(git_show(repo, base+'manifest.json'))
    parts = []
    for part in manifest['ordered_parts']:
        b = git_show(repo, base+part['path'])
        if len(b) != part['bytes'] or digest(b) != part['sha256']:
            raise ValueError(f'archive part mismatch: {batch}/{part["path"]}')
        parts.append(b)
    blob = b''.join(parts)
    if digest(blob) != manifest['archive_sha256'] or digest(blob) != PACKS[batch]:
        raise ValueError(f'pinned archive mismatch: {batch}')
    return manifest, tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz')

def member(tar, name):
    if '/WCPP/' in name:
        name = 'WCPP/' + name.split('/WCPP/', 1)[1]
    if not name.startswith('WCPP/'):
        raise ValueError(f'freeze path outside archived WCPP tree: {name}')
    return name

def first_refs(batch, tf, verify):
    names = tf.getnames()
    def load(path): return json.loads(tf.extractfile(path).read())
    if batch.startswith('20261107'):
        index_path, = [p for p in names if p.endswith('/analysis-records.json') and 'night19-20261107-1935' in p]
        index = load(index_path); refs=[]
        for item in index['matches']:
            ref=item['comprehensive_reference']
            refs.append((ref['path'],ref['sha256'],ref['freeze_id'],ref['recorded_at'],ref['evaluation_slot']))
    else:
        index_path, = [p for p in names if p.endswith('/analysis-records.json') and 'night19-20261108-1830' in p]
        # The summary index carries the current-test-pair track too. The native
        # feed under study is the comprehensive track, whose own v1 record must
        # also assert FIRST_PREMATCH. Select only that exact first revision.
        candidates=[]
        for name in names:
            if not (name.startswith('WCPP/forecasts/issued-analyses/') and name.endswith('.json')): continue
            try: rec=load(name)
            except Exception: continue
            f=rec.get('fixture') or {}
            if rec.get('track')=='comprehensive' and rec.get('stage')=='prematch' and f.get('match_id'):
                if rec.get('revision')==1 and rec.get('evaluation_slot')=='FIRST_PREMATCH':
                    candidates.append((f['match_id'],name,rec))
        earliest={}
        for mid,name,rec in candidates:
            old=earliest.get(str(mid))
            if old is None or aware(rec['recorded_at'])<aware(old[2]['recorded_at']):earliest[str(mid)]=(mid,name,rec)
        refs=[]
        for mid,name,rec in earliest.values():
            refs.append((name,rec['record_sha256'],rec['freeze_id'],rec['recorded_at'],rec['evaluation_slot']))
    out=[]
    for path,sha,freeze_id,recorded,slot in refs:
        m=member(tf,path);b=tf.extractfile(m).read();rec=verify(json.loads(b))
        if rec.get('record_sha256') != sha: raise ValueError('freeze record hash mismatch')
        if (slot != 'FIRST_PREMATCH' or rec.get('evaluation_slot') != 'FIRST_PREMATCH' or
            rec.get('revision') != 1 or rec.get('freeze_id') != freeze_id or rec.get('recorded_at') != recorded or
            rec.get('stage') != 'prematch'):
            raise ValueError(f'first-freeze reference/evaluation slot mismatch: batch={batch}, ref_slot={slot}, record_slot={rec.get("evaluation_slot")}, revision={rec.get("revision")}, freeze_id={rec.get("freeze_id")}, ref_id={freeze_id}, track={rec.get("track")}, stage={rec.get("stage")}')
        f=rec['fixture']
        if not aware(f['cutoff_at']) <= aware(recorded) < aware(f['kickoff_at']):
            raise ValueError('first freeze timing invalid')
        out.append((f,rec,sha,m))
    return out,index_path

def summary_metadata(tf):
    out={}
    for n in tf.getnames():
        if not n.endswith('.meta.json'): continue
        try: m=json.loads(tf.extractfile(n).read())
        except Exception: continue
        if m.get('sha256') and m.get('url'):
            out[(m['sha256'],m['url'])]=(n,m)
    return out

def bind_summary(tf, fixture, rec, archive_files, project_quote):
    srcs=[]
    for s in fixture.get('sources') or []:
        u=s.get('source_url') or '';parts=urlsplit(u);q=parse_qs(parts.query)
        if parts.hostname=='site.api.espn.com' and parts.path.endswith('/summary') and q.get('event'):
            srcs.append((s,q['event'][0]))
    if len(srcs)!=1: return None, 'NO_UNIQUE_BOUND_ESPN_SUMMARY_SOURCE'
    s,eid=srcs[0];u=s['source_url'];sha=s['response_sha256'];captured=aware(s['query_time'])
    cutoff,kick=aware(fixture['cutoff_at']),aware(fixture['kickoff_at'])
    if captured>cutoff or cutoff>=kick: return None,'SOURCE_NOT_AVAILABLE_BY_PREMATCH_CUTOFF'
    meta_entry=archive_files.get((sha,u))
    if not meta_entry: return None,'CAPTURE_SIDECAR_NOT_FOUND'
    meta_path,meta=meta_entry
    if meta.get('http_status') not in (None,200) or aware(meta.get('queried_at') or meta.get('query_time') or meta.get('query_time_utc'))!=captured:
        return None,'CAPTURE_SIDECAR_CLOCK_OR_STATUS_MISMATCH'
    rawname=member(tf,s.get('source_file') or s['raw_file']);raw=tf.extractfile(rawname).read()
    if digest(raw)!=sha: raise ValueError(f'bound original raw SHA mismatch: {eid}')
    item=json.loads(raw);header=item.get('header') or {};comps=header.get('competitions') or []
    if len(comps)!=1 or str(header.get('id'))!=str(eid): return None,'ESPN_HEADER_EVENT_MISMATCH'
    comp=comps[0];league=header.get('league') or {};season=header.get('season') or {}
    if league.get('slug')!=fixture.get('competition') or str(comp.get('id'))!=str(eid): return None,'ESPN_NATIVE_LEAGUE_OR_COMP_MISMATCH'
    if season.get('year') is None or season.get('type') is None or not season.get('name'):
        return None,'ESPN_NATIVE_SEASON_METADATA_MISSING'
    if comp.get('status',{}).get('type',{}).get('state')!='pre': return None,'ESPN_PHASE_NOT_PRE'
    sides={x.get('homeAway'):x.get('team') or {} for x in comp.get('competitors') or []}
    home,away=sides.get('home') or {},sides.get('away') or {}
    freeze_home=str(fixture.get('home') or '').removeprefix('espn:')
    freeze_away=str(fixture.get('away') or '').removeprefix('espn:')
    names_match=(str(home.get('id'))==freeze_home and str(away.get('id'))==freeze_away)
    if not names_match and not (fixture.get('home_name')==home.get('displayName') and fixture.get('away_name')==away.get('displayName')):
        return None,'ESPN_HOME_AWAY_IDENTITY_MISMATCH'
    if aware(comp.get('date'))!=kick:
        return None,'ESPN_HOME_AWAY_OR_KICKOFF_MISMATCH'
    # MLS is accepted only with native regular-season metadata; other rows need a
    # regular freeze phase, same ESPN league, season identity and prematch event.
    if fixture.get('stage')!='regular': return None,'FREEZE_STAGE_OUTSIDE_REGULAR_PHASE'
    if fixture.get('competition')=='usa.1' and 'regular season' not in season['name'].casefold():
        return None,'ESPN_MLS_SEASON_NOT_REGULAR'
    quote=project_quote(item.get('odds'))
    row={'home_id':'espn:'+str(home.get('id')),'away_id':'espn:'+str(away.get('id')),'source_ids':{'freeze_match_id':fixture['match_id'],'espn_event_id':'espn:'+str(eid)},
         'capture_at':captured.isoformat().replace('+00:00','Z'),'raw_sha256':sha,'raw_archive_member':rawname,
         'capture_meta_sha256':digest(tf.extractfile(meta_path).read()),'capture_meta_archive_member':meta_path,
         'source_url':u,'source_query_time_utc':captured.isoformat().replace('+00:00','Z'),
         'native_season_year':season['year'],'native_season_type':season['type'],'native_season_name':season['name'],
         'native_phase':comp['status']['type']['state'],'quote_timestamp':'UNKNOWN; capture is not bookmaker quote time',**quote}
    if not row['latest_h2h'] or any(row['latest_h2h'].get(k) is None for k in ('home','draw','away')):
        return row,'DRAFTKINGS_H2H_INCOMPLETE'
    if not row['current_total'] or any(row['current_total'].get(k) is None for k in ('line','over','under')):
        return row,'DRAFTKINGS_CURRENT_TOTAL_INCOMPLETE'
    # Missing opening total is deliberately retained, not used to drop the row.
    return row,None

def export(data, source, out):
    verify,project_quote=helpers(source);rows=[];batch_meta={};batch_ids={}
    for batch in ('20261107-night19-55matches','20261108-night19-46matches'):
        manifest,tf=archive(data,batch);refs,indexpath=first_refs(batch,tf,verify);metas=summary_metadata(tf)
        batch_ids[batch]={str(f['match_id']) for f,_,_,_ in refs}
        batch_meta[batch]={'archive_sha256':manifest['archive_sha256'],'source_commit':manifest.get('source_commit'),'first_refs':len(refs),'index_member':indexpath}
        for f,rec,freeze_sha,freeze_path in refs:
            kick=aware(f['kickoff_at']);cut=aware(f['cutoff_at']);comp=f.get('competition')
            row={'id':f['match_id'],'date':kick.astimezone(timezone(timedelta(hours=8))).isoformat(),
                 'split':'train' if kick<SPLIT else 'post_split','competition':comp,'provider':'DraftKings',
                 'home_id':None,'away_id':None,'source_ids':{'freeze_match_id':f['match_id'],'espn_event_id':None},
                 'cutoff_at':cut.isoformat().replace('+00:00','Z'),'kickoff_at':kick.isoformat().replace('+00:00','Z'),
                 'recorded_at':aware(rec['recorded_at']).isoformat().replace('+00:00','Z'),'capture_at':None,
                 'freeze_sha256':freeze_sha,'freeze_record_sha256':freeze_sha,'freeze_record_path':freeze_path,
                 'data_snapshot_commit':DATA_REV,'batch_archive_sha256':manifest['archive_sha256'],
                 'freeze_revision':rec['revision'],'evaluation_slot':rec['evaluation_slot'],
                 'analysis_index_member':indexpath,'freeze_stage':f.get('stage'),'stage_identity_verified':f.get('identity_verified') is True,
                 'trusted_stage':None,'project_scope':'IN_SCOPE' if comp in SCOPE else 'OUT_OF_SCOPE',
                 'raw_sha256':None,'raw_archive_member':None,'capture_meta_sha256':None,'capture_meta_archive_member':None,
                 'source_url':None,'source_query_time_utc':None,'latest_h2h':None,'opening_total':None,'current_total':None,
                 'excluded':None,'batch':batch}
            if not row['stage_identity_verified']: row['excluded']='FREEZE_FIXTURE_IDENTITY_UNVERIFIED'
            elif comp not in SCOPE: row['excluded']='COMPETITION_OUTSIDE_FORMAL_20'
            elif f.get('stage')!='regular': row['excluded']='FREEZE_STAGE_OUTSIDE_REGULAR_PHASE'
            bound,why=bind_summary(tf,f,rec,metas,project_quote)
            if bound:
                row.update(bound);row['source_ids']['espn_event_id']=bound['source_ids']['espn_event_id'];row['trusted_stage']='regular'
            if why: row['excluded']=row['excluded'] or why
            rows.append(row)
        tf.close()
    # Verify the later Big5 archive identity and count only; its revision-four
    # records are not first-freeze inputs and must not overwrite the night46 rows.
    big_batch='20261108-big5-remaining-22matches'
    manifest,tf=archive(data,big_batch)
    name,= [n for n in tf.getnames() if n.endswith('/analysis-records.json') and 'big5-remaining-20261108-2103' in n]
    big_index=json.loads(tf.extractfile(name).read());big_ids=set()
    for item in big_index['matches']:
        ref=item['pair'];rec=verify(json.loads(tf.extractfile(member(tf,ref['path'])).read()))
        if (rec.get('record_sha256')!=ref['sha256'] or rec.get('freeze_id')!=ref['freeze_id'] or
            rec.get('recorded_at')!=ref['recorded_at'] or rec.get('evaluation_slot')!=ref['evaluation_slot'] or
            ref.get('evaluation_slot')!='SEPARATE_REVISION_OR_STAGE'):
            raise ValueError('Big5 refresh reference identity/slot mismatch')
        big_ids.add(str(rec['fixture']['match_id']))
    subset=len(big_ids & batch_ids['20261108-night19-46matches'])
    if len(big_ids)!=22 or subset!=22: raise ValueError('Big5 refresh is not an exact 22-ID later-revision subset')
    batch_meta[big_batch]={'archive_sha256':manifest['archive_sha256'],'source_commit':manifest.get('source_commit'),
                           'revision_rows_audit_only':len(big_ids),'exact_subset_of_night46':subset,'index_member':name}
    tf.close()
    # Keep earliest authentic freeze on duplicate event IDs; later 22-row refresh is audit-only.
    earliest={}
    for r in rows:
        key=r['id'];old=earliest.get(key)
        if old is None or aware(r['recorded_at'])<aware(old['recorded_at']):earliest[key]=r
    final=sorted(earliest.values(),key=lambda r:(r['kickoff_at'],r['competition'],r['id']))
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in final),encoding='utf-8')
    return final,batch_meta

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    rows,meta=export(a.data,a.source,a.out)
    from collections import Counter
    print(json.dumps({'rows':len(rows),'source_bound':sum(bool(r['raw_sha256']) for r in rows),'included':sum(r['excluded'] is None for r in rows),'excluded':dict(Counter(r['excluded'] for r in rows if r['excluded'])),'batches':meta},indent=2))
if __name__=='__main__': main()
