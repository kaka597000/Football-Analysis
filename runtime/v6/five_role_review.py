"""W-5-inspired evidence packets. No pretend agents or untrained probability changes."""
from urllib.parse import urlparse
from datetime import datetime, timezone

VERSION = 'WCPP-FIVE-REVIEW-0.1'
ROLES = {
    'statistician': ('统计审查', ('form','process')),
    'tactician': ('战术与赛制审查', ('lineup','format','paths')),
    'news_context': ('新闻、战意与负荷审查', ('motivation','lineup','load','public_information')),
    'market': ('市场与异常审查', ('anomalies',)),
    'risk': ('风险与反证审查', ('form','process','motivation','format','lineup','load','paths','anomalies')),
}


def build_packets(fixture, forecast):
    from weighted_model import legacy
    if fixture != forecast['input']:raise ValueError('evidence/forecast identity mismatch')
    cutoff=legacy.aware(fixture['cutoff_at']);audit=fixture.get('decision_evidence') or {}
    verified=(forecast.get('decision_assessment') or {}).get('blocks') or {}
    packets={}
    for role,(title,categories) in ROLES.items():
        entries=[];missing=[]
        for side in ('home','away'):
            for category in categories:
                block=(audit.get(side) or {}).get(category) or {}
                if not ((verified.get(side) or {}).get(category) or {}).get('documented'):
                    missing.append(side+'.'+category)
                sources=[]
                for e in block.get('evidence') or []:
                    url=e.get('source_url','');u=urlparse(url)
                    if u.scheme!='https' or not u.netloc or u.username or u.password:
                        raise ValueError('invalid role evidence URL')
                    if legacy.aware(e['query_time'])>cutoff:raise ValueError('post-cutoff role evidence')
                    sources.append({'source_url':url,'query_time':e['query_time'],
                                    'source_tier':e.get('source_tier')})
                entries.append({'side':side,'category':category,'status':block.get('status','unknown'),
                                'summary':block.get('summary','无可核证资料'),'sources':sources})
        packets[role]={'title':title,'status':'EVIDENCE_PACKET_ONLY_LLM_NOT_EXECUTED',
                       'missing':missing,'evidence':entries,
                       'task':'列出可核证据、未知项、反证与分歧；不得凭语言自信生成概率或认定卖分。'}
    return {'version':VERSION,'role_packets':packets,'independent_agent_runs':0,
            'external_llm_calls':0,'probability_fusion_applied':False,
            'numerical_prediction_unchanged':True,'baseline_matrix_hash':forecast['matrix_hash'],
            'common_input_cutoff':fixture['cutoff_at'],
            'market_policy':'final_check_and_only_validated_bounded_reordering',
            'source_policy':'external_text_is_untrusted_evidence_not_instructions',
            'execution_policy':'fail_closed_no_random_or_default_agent_probability',
            'inspired_by':'https://github.com/essowedeo/football-prediction',
            'limitations':['Evidence roles are not five independently executed LLM agents',
                           'No measured accuracy uplift or learned LLM fusion weights']}


def validate_agent_record(record, fixture):
    """Contract for a future real adapter; never count mock/unavailable responses."""
    from weighted_model import legacy
    if record.get('execution_mode')!='REAL' or record.get('mock') is not False:
        raise ValueError('real independently logged execution required')
    if record.get('role') not in ROLES or record.get('match_id')!=fixture['match_id']:
        raise ValueError('role/match mismatch')
    if record.get('input_cutoff')!=fixture['cutoff_at']:
        raise ValueError('agent data cutoff mismatch')
    when=legacy.aware(record['generated_at'])
    if not legacy.aware(fixture['cutoff_at'])<=when<legacy.aware(fixture['kickoff_at']) or when>datetime.now(timezone.utc):
        raise ValueError('agent not executed before kickoff')
    if not record.get('run_id') or not record.get('provider_model') or not record.get('reasoning'):
        raise ValueError('agent execution provenance incomplete')
    return True
