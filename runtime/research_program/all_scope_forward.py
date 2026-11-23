"""Issue the locked 20-competition policies and fixed-recipe refits on a registered future slate."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import unicodedata

from research.analysis_freeze import DEFAULT_ROOT, aware, digest
from . import all_scope_model as model
from . import all_scope_neighbors as neighbors
from .goal_state_process import combine_heads
from .all_scope_study import forecasts, half_life, restore, tree_delta
from .goal_state_forward import register, issue
from .goal_state_study import read_lines, sha, write_new

TRACK='all-scope-score-20261123-first'


def key(name):
    return ''.join(c for c in unicodedata.normalize('NFKD',name.casefold()) if c.isalnum())


def phase(f, known):
    code, source=f['competition'],f['stage'].lower()
    slug=(f['season'].get('slug','') if isinstance(f['season'],dict) else '').lower()
    if code=='usa.1' and 'playoff' in slug:
        return 'playoffs:'+slug
    if code=='kor.1' and ('파이널' in source or 'final' in source):
        group='a' if source.endswith('a') else 'b' if source.endswith('b') else None
        if group:
            for candidate in ('split-'+group,'split-final-'+group,'split-final:'+group,'final-'+group):
                if candidate in known:
                    return candidate
        return 'split-final'
    if code=='afc.champions':
        for region in ('east','west'):
            if region in source:
                for candidate in ('elite:league-phase:'+region+'-region','league-phase:'+region,'league-stage:'+region,'league-phase-'+region):
                    if candidate in known:
                        return candidate
        return 'league-phase' if 'league' in slug else slug
    if code.startswith('uefa.') and code!='uefa.nations':
        return 'league-phase' if ('league' in source and 'phase' in source) else slug
    return 'regular'


def prices(row):
    # Underlying scraper presents a single bookmaker; take its latest observed listing.
    hda=total=label=None
    for when in ('close','open'):
        snapshot=(row.get('h2h') or {}).get(when) or {}
        hd=snapshot.get('prices',snapshot)
        combined={s:(hd.get(s) or {}).get('decimal') for s in model.SIDES}
        ou=(row.get('total') or {}).get(when) or {}
        over,under=ou.get('over') or {},ou.get('under') or {}
        has_hd=bool(model._prices(combined,model.SIDES))
        has_ou=(over.get('line') is not None and over['line']==under.get('line')
                and model._prices({'over':over.get('decimal'),'under':under.get('decimal')},('over','under')))
        if has_hd and has_ou and row.get('paired_market',True):
            return combined,dict(line=over['line'],over=over['decimal'],under=under['decimal']),True,when
        if hda is None and has_hd:
            hda,label=combined,when
        if total is None and has_ou:
            total=dict(line=over['line'],over=over['decimal'],under=under['decimal'])
    line_only=row.get('observed_line_only')
    if total is None and isinstance(line_only,dict) and model.actual_line({'total':line_only}) is not None:
        total={'line':line_only['line']}
    return hda,total,False,label


def load_run(folder):
    lock=json.loads((folder/'lock.json').read_bytes())
    history=json.loads((folder/'history-state.json').read_bytes())
    refresh=json.loads((folder/'refresh.json').read_bytes())
    original=json.loads((folder/'artifact.json').read_bytes())
    if (sha(folder/'artifact.json')!=lock['artifact_sha256'] or history['artifact_sha256']!=lock['artifact_sha256']
            or sha(folder/'history-state.json')!=refresh['history_state_sha256'] or refresh['policy']!=lock['policy']):
        raise ValueError('prediction depends on different locked artifacts or history')
    states,trees=restore(dict(states=history['states'],trees=original['trees']))
    _,refit=restore(dict(states={},trees=refresh['trees']))
    neighbor=json.loads((folder/'neighbor-history.json').read_bytes())
    if neighbor['panel_sha256']!=history['input_sha256']:
        raise ValueError('local score model uses different matches')
    memories={k:neighbors.Memory(v) for k,v in neighbor['memories'].items()}
    return states,trees,refit,lock['policy'],memories,neighbor['policy']


def team(f,side,teams,aliases):
    raw=f[side]
    mapped=aliases.get(f['competition'],{}).get(str(f[side+'_id']))
    if mapped in teams:
        return mapped,'verified_id'
    if raw in teams:
        return raw,'literal'
    equivalents=[t for t in teams if key(raw)==key(t)]
    if len(equivalents)==1:
        return equivalents[0],'normalized_literal'
    return raw,'new_to_phase'


def predict(f,market,run,aliases,cutoff):
    states,trees,refit,policy,memories,neighbor_policy=run
    stage=phase(f,states)
    state=deepcopy(states[stage]) if stage in states else model.blank_state()
    day=aware(f['kickoff_at']).astimezone(timezone.utc).date().toordinal()
    hl=half_life(f['competition'])
    model.advance(state,day,hl)
    home,hm=team(f,'home',state['teams'],aliases)
    away,am=team(f,'away',state['teams'],aliases)
    if home==away:
        raise ValueError('identical model team identity')
    evidence=market.get('market_evidence') or {}
    if f['identity_verified'] is not True or not aware(f['observed_at'])<=aware(cutoff)<aware(f['kickoff_at']):
        raise ValueError('unverified or late schedule observation')
    hda,total,paired,label=prices(market)
    if (total or hda) and not evidence:
        evidence={}
        for market_name in ('h2h','total'):
            q=(market.get(market_name) or {}).get('close') or {}
            if q:
                src=q['source']
                evidence[market_name]=dict(bookmaker=q['bookmaker'],source_sha256=src['sha256'],source_url=src['url'],
                    observed_at=q['observed_at'],quoted_at=q.get('quoted_at'),snapshot_label=q.get('snapshot_label'))
        if not evidence and isinstance(market.get('observed_line_only'),dict):
            q=market['observed_line_only']
            evidence['total']=dict(source_sha256=q['source']['sha256'],observed_at=q['observed_at'])
    if total or hda:
        refs=[evidence] if 'source_sha256' in evidence else list(evidence.values())
        if not refs or any(not x.get('source_sha256') or not aware(x['observed_at'])<=aware(cutoff) for x in refs):
            raise ValueError('late or untraceable market observation')
    game=dict(home=home,away=away,neutral=f.get('neutral'),h2h=hda,total=total,paired_market=paired)
    prepared=model.prepare(state,game,day,hl)
    def decisions(models):
        delta=tree_delta(models.get(stage),[prepared['features']])
        return forecasts(state,prepared,None if delta is None else delta[0],game,policy)
    picks=decisions(trees)
    picks['refreshed']=decisions(refit)['candidate']
    local=neighbors.predict(memories,stage,game['neutral'],prepared,day,hl)
    picks['neighbor']=combine_heads({'local':local,'original':picks['candidate']},neighbor_policy)
    return dict(forecasts={k:picks[k] for k in ('candidate','neighbor','refreshed','market','team')},
                line=prepared['line'],model_stage=stage,source_stage=f['stage'],source_season=f['season'],
                policy=policy,neighbor_policy=neighbor_policy,home_name=f['home'],away_name=f['away'],model_teams=[home,away],
                team_mapping=[hm,am],phase_seen=stage in states,market_mode=prepared['kind'],
                displayed_label=label,prices=dict(h2h=hda,total=total),market_evidence=evidence,
                fixture_sources=f['sources'],neutral=f.get('neutral'),status='PENDING')


def run(study,fixtures,markets,aliases,out,freeze_root=DEFAULT_ROOT):
    schedule=list(read_lines(fixtures))
    market_rows=list(read_lines(markets))
    market={v['match_id']:v for v in market_rows}
    if len(market)!=len(market_rows) or {f['match_id'] for f in schedule}!=set(market):
        raise ValueError('future fixtures and market status must cover the same unique population')
    aliases=json.loads(aliases.read_bytes())
    def identity(f):
        source='kleague' if f['match_id'].startswith('kleague:') else 'espn'
        return dict(match_id=f['match_id'],cohort=f['competition'],stage=f['stage'],kickoff_at=f['kickoff_at'],
                    home=source+':'+f['home_id'],away=source+':'+f['away_id'])
    codes=sorted({r['competition'] for r in schedule})
    models={c:load_run(study/c) for c in codes}
    input_hashes={c:{name:sha(study/c/name) for name in ('artifact.json','history-state.json','refresh.json','lock.json','neighbor-lock.json','neighbor-history.json')} for c in codes}
    all_codes=sorted(p.parent.name for p in study.glob('*/lock.json'))
    if not set(codes).issubset(all_codes):
        raise ValueError('unregistered future competition')
    ident=dict(model=model.VERSION,competitions=all_codes,study_sha256=input_hashes,schedule_sha256=sha(fixtures),
               market_status_sha256=sha(markets),team_aliases_sha256=digest(aliases),
               pairs={'locked_vs_market':['market','candidate'],'refreshed_vs_locked':['candidate','refreshed'],
                      'locked_vs_team':['team','candidate'],'neighbor_vs_locked':['candidate','neighbor'],'neighbor_vs_market':['market','neighbor']})
    plan=register([identity(f) for f in schedule],ident,TRACK,out,freeze_root=freeze_root)
    items={}
    for f in schedule:
        mid=f['match_id']
        fixture=identity(f)
        fixture['cutoff_at']=datetime.now(timezone.utc).isoformat()
        pred=predict(f,market[mid],models[f['competition']],aliases,fixture['cutoff_at'])
        items[mid]=issue(plan,fixture,pred,TRACK,out,freeze_root=freeze_root)
    write_new(out/'receipts.json',dict(registration=plan,matches=items))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for keyname in ('study','fixtures','markets','aliases','out'):
        p.add_argument('--'+keyname,type=Path,required=True)
    p.add_argument('--freeze-root',type=Path,default=DEFAULT_ROOT)
    args=p.parse_args()
    run(**vars(args))
