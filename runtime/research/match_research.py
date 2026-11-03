"""Collect research execution status and run an explicitly requested simulation."""
import copy


def research_execution(fixture, pair):
    research = {}
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
    return research['execution']
