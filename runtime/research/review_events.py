"""Goal ownership for post-match review, validated against score movement.

FotMob isHome identifies the credited goal side; for an own goal that is not
its player's team. This module never edits a forecast or a provider response.
"""

def _score(values):
    if not isinstance(values,(list,tuple)) or len(values)!=2 or any(type(x)is not int or x<0 for x in values):
        raise ValueError('two nonnegative integer score components required')
    return tuple(values)


def normalize_fotmob_goal(event, *, home_team_id, away_team_id):
    if event.get('type')!='Goal' or event.get('isPenaltyShootoutEvent'):
        raise ValueError('regulation match goal event required; shootout excluded')
    before=_score([event.get('homeScore'),event.get('awayScore')]);after=_score(event.get('newScore'))
    delta=tuple(a-b for a,b in zip(after,before))
    if delta not in [(1,0),(0,1)]:
        raise ValueError('goal must increase exactly one team score by one')
    shot=event.get('shotmapEvent') or {}
    own=event.get('ownGoal')
    if own is None:
        own=shot.get('isOwnGoal')
    if type(own)is not bool:raise ValueError('explicit boolean own-goal flag required')
    credited_home=delta==(1,0)
    if type(event.get('isHome'))is not bool or event['isHome']!=credited_home:
        raise ValueError('provider credited side conflicts with score movement')
    beneficiary='home' if credited_home else 'away'
    actor=('away' if credited_home else 'home') if own else beneficiary
    if shot.get('isOwnGoal') is not None and shot['isOwnGoal']!=own:
        raise ValueError('shot and event own-goal flags conflict')
    if shot.get('teamId') is not None:
        expected=home_team_id if actor=='home' else away_team_id
        if str(shot['teamId'])!=str(expected):
            raise ValueError('player team contradicts shot-map team identity')
    minute=str(event.get('timeStr',event.get('time')))
    if event.get('overloadTime'):minute+=' + '+str(event['overloadTime'])
    return {'minute':minute,'type':'Goal','detail':{
        'scorer':event.get('fullName') or event.get('player',{}).get('name'),
        'team_side':actor,'beneficiary_side':beneficiary,'own_goal':own,
        'assist':event.get('assistStr'),'score_before':list(before),'score_after':list(after)},
        'normalization':'SCORE_DELTA_AND_SHOT_TEAM_ID_VERIFIED'}
