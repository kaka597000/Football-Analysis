"""Compose single-match evidence reports while preserving independent models.

Public helpers remain importable here for existing callers. Validation, optional
research execution and Markdown rendering live in dedicated modules.
"""
import copy

from .match_evidence import aware, evidence_block
from .match_report import render
from .match_research import research_execution

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
    result['research']['execution'] = research_execution(fixture, pair)
    # Existing real numerical output must never be relabelled by an evidence summary.
    return {'schema':'WCPP-SINGLE-MATCH-ANALYSIS-1','match_id':fixture['match_id'],
            'cutoff_at':fixture['cutoff_at'],'modules':result,
            'missing_or_conflicting_modules':[k for k,v in result.items() if v['status'] in ('unknown','conflict','invalid')],
            'numeric_prediction_modified':False,'automatic_primary_selected':False,
            'selection_checklist':['逐队比较0/1/2+进球路径','比较至少两个比分候选及反证',
              '重点核查同机构同盘口水位变化与升降盘','胜平负、第一比分和实际大小球分别判断',
              '引用双模型冻结，综合判断独立追加；缺数据如实标记'],
            'notice':'模块有输出不代表数据齐全、五名外部代理已执行、状态模拟已训练或准确率提高'}
