"""Render a single-match evidence report without executing models."""
import json


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
