"""One-match evidence inventory and report; preserves independent numeric models."""
from datetime import datetime
from urllib.parse import urlsplit,parse_qsl
import copy
import json

MODULES = {
 'history':'历史进失球、球队攻防、主客场',
 'form':'近期状态与比赛间隔',
 'shots':'射门、射正、控球',
 'xg':'xG / xGA',
 'lineup':'阵容、伤停、停赛',
 'context':'战意、旅行、完整赛程负荷',
 'market':'盘口、赔率与水位变化',
 'research':'五角色审查、分钟模拟、冷门旁路',
}


def aware(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('timezone required')
    return d


def evidence_block(block, fixture):
    from .analysis_freeze import reject_credentials
    reject_credentials(block)
    if block.get('status') not in ('available','partial','scenario','unknown','conflict'):raise ValueError('status')
    if not isinstance(block.get('summary'),str) or not block['summary'].strip():raise ValueError('summary')
    if not isinstance(block.get('facts',{}),dict):raise ValueError('facts')
    if block.get('match_id')!=fixture['match_id']:raise ValueError('match identity')
    cutoff=aware(fixture['cutoff_at'])
    if aware(block['as_of'])>cutoff:raise ValueError('late module')
    if block['status'] in ('available','partial','scenario'):
        if not block.get('sources') or not block.get('facts'):raise ValueError('source-backed facts required')
    for e in block.get('sources',[]):
        u=urlsplit(e['source_url'])
        if (u.scheme!='https' or not u.hostname or u.username or u.password
                or any(k.lower() in {'key','apikey','api_key','token','access_token'} for k,_ in parse_qsl(u.query))):raise ValueError('source')
        if not aware(e['known_at'])<=aware(e['query_time'])<=aware(block['as_of'])<=cutoff:raise ValueError('source time')
    if block['status']=='scenario' and (not block.get('scenarios') or not block.get('uncertainty')):raise ValueError('scenario uncertainty')
    return {k:copy.deepcopy(block[k]) for k in ('status','summary','facts','sources','missing','scenarios','uncertainty') if k in block}


def build(fixture, pair, *, current_artifact, test_artifact):
    inputs=fixture.get('analysis_modules') or {};result={}
    artifacts={'current':current_artifact,'test':test_artifact}
    for name,title in MODULES.items():
        entry={'title':title,'status':'unknown','summary':'缺少该模块的本场可核查证据','facts':{},'sources':[],
               'status_scope':'SUPPLEMENTARY_MATCH_EVIDENCE','numerical_weight':None,'evidence_usage':'MISSING_NOT_ZERO_FILLED'}
        if name in inputs:
            try:
                entry.update(evidence_block(inputs[name],fixture));entry['evidence_usage']='QUALITATIVE_EVIDENCE_ONLY'
            except (ValueError,TypeError,KeyError,AttributeError):
                entry.update(status='invalid',summary='模块证据无效；核查赛事身份、来源、时点与敏感字段')
        result[name]=entry
    result['history']['numeric_baselines']={k:{'version':pair[k+'_model'].get('version'),
            'training_cutoff':pair[k+'_model'].get('training_data_cutoff'),
            'team_goals':copy.deepcopy(pair[k+'_model'].get('team_goal_analysis'))} for k in artifacts}
    result['form']['numeric_features']='模型自身历史特征依各工件执行；最新补充事实只作定性证据'
    for name,field in [('shots','feature_process_match_count'),('xg','feature_xg_match_count')]:
        result[name]['recorded_training_rows']={k:a.get(field) for k,a in artifacts.items()}
        result[name]['training_notice']='工件未登记的覆盖保持未知；0表示本工件无登记合格训练行，不表示未采集任何资料'
    result['lineup']['policy']='未公布首发不自动拒绝；预计/未知用情景比较，重大未解决风险另行待定'
    from .lineup_evidence import assess as assess_lineup
    result['lineup']['scenario_assessment']={side:assess_lineup(((fixture.get('decision_evidence') or {}).get(side) or {}).get('lineup') or {},fixture) for side in ('home','away')}
    result['context']['policy']='不把同联赛比赛间隔冒充完整体能；无数学核验不宣称已出线或出局'
    result['market']['priority']='HIGH_EVIDENCE_REVIEW_NOT_FIXED_NUMERIC_WEIGHT'
    result['market']['movement']=copy.deepcopy(pair.get('market_movement'))
    research=result['research']
    research['execution']={
        'five_role_review':copy.deepcopy(pair['test_model'].get('five_role_review',{'status':'UNAVAILABLE'})),
        'upset_classifier':copy.deepcopy(pair.get('upset_classifier')),
        'upset_candidate':copy.deepcopy(pair.get('upset_candidate')),
        'selection_experiment':copy.deepcopy(pair.get('selection_experiment')),
        'minute_simulation':{'status':'NOT_CONFIGURED','reason':'无独立来源支持的分钟率/状态输入；不伪造状态权重'},
    }
    request=fixture.get('minute_simulation_request')
    if request is not None:
        try:
            from .minute_simulator import simulate
            if request['artifact']['cohort']!=fixture['cohort']:raise ValueError('cohort')
            if fixture.get('phase')=='PreMatch' and request['state']['minute']!=0:raise ValueError('live state in prematch')
            research['execution']['minute_simulation']=simulate(request['artifact'],request['state'],
                as_of=fixture['cutoff_at'],draws=20000,seed=0,
                regulation_minutes=request['regulation_minutes'],state_artifact=request.get('state_artifact'))
        except (ValueError,KeyError,TypeError,AttributeError,OSError):
            research['execution']['minute_simulation']={'status':'REFUSED_INVALID_OR_UNTRAINED_INPUT','prediction_modified':False}
    # Existing real numerical output must never be relabelled by an evidence summary.
    return {'schema':'WCPP-SINGLE-MATCH-ANALYSIS-1','match_id':fixture['match_id'],
            'cutoff_at':fixture['cutoff_at'],'modules':result,
            'missing_or_conflicting_modules':[k for k,v in result.items() if v['status'] in ('unknown','conflict','invalid')],
            'numeric_prediction_modified':False,'automatic_primary_selected':False,
            'selection_checklist':['逐队比较0/1/2+进球路径','比较至少两个比分候选及反证',
              '重点核查同机构同盘口水位变化与升降盘','胜平负、第一比分和实际大小球分别判断',
              '引用双模型冻结，综合判断独立追加；缺数据如实标记'],
            'notice':'模块有输出不代表数据齐全、五名外部代理已执行、状态模拟已训练或准确率提高'}


def render(report):
    lines=['# 单场赛事完整模块分析', '',f"赛事ID：{report['match_id']}；截止时间：{report['cutoff_at']}",'',report['notice'],'']
    for k,v in report['modules'].items():
        lines.extend(['## '+v['title'],'','补充证据状态：'+v['status'],v['summary'],''])
        if v.get('facts'):lines.extend(['本场事实：','```json',json.dumps(v['facts'],ensure_ascii=False,indent=2,allow_nan=False),'```',''])
        for source in v.get('sources',[]):lines.append('- 来源：'+source['source_url']+'；已知时间：'+source['known_at']+'；抓取：'+source['query_time'])
        for field in ('missing','scenarios','uncertainty'):
            if v.get(field):lines.append(field+'：'+json.dumps(v[field],ensure_ascii=False))
        if 'numeric_baselines' in v:
            for role,b in v['numeric_baselines'].items():
                lines.append(f"- {role}：{b['version']}；训练比赛截止 {b['training_cutoff']}")
                goals=b.get('team_goals') or {}
                for side in ('home','away'):
                    a=goals.get(side) or {}
                    if a:lines.append(f"  - {side}：均值 {a.get('mean')}；P0 {a.get('p0')}；P≥2 {a.get('p_ge_2')}；P≥3 {a.get('p_ge_3')}；区间 {a.get('central_interval')}")
        if 'recorded_training_rows' in v:lines.append('工件记录的训练覆盖行数：'+json.dumps(v['recorded_training_rows'],ensure_ascii=False)+'；'+v['training_notice'])
        if 'movement' in v:lines.append('同机构同盘口价格比较：'+json.dumps(v['movement'],ensure_ascii=False,allow_nan=False))
        if 'execution' in v:
            for name,x in v['execution'].items():
                x=x or {};status=x.get('status') or x.get('execution_policy') or '见完整JSON执行状态'
                lines.append('- '+name+'：'+str(status))
                if name=='five_role_review':lines.append('  外部调用次数：'+str(x.get('external_llm_calls','未知'))+'；独立代理次数：'+str(x.get('independent_agent_runs','未知')))
        lines.extend(['数值权重：未在本报告另行指定；数据缺失不填零。',''])
    lines.extend(['## 综合选分检查','',*['- '+s for s in report['selection_checklist']],
                  '', '本报告是证据与执行状态报告，不自动将统计比分改名为综合第一推荐；综合结论须独立保存冻结。'])
    return '\n'.join(lines)+'\n'
