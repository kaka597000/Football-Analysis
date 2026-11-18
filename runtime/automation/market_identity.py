"""ESPN fixture sides and time-qualified team aliases shared by the market feeds."""
try:
    from runtime.v2_test.data_contract import aware
except ModuleNotFoundError:
    from v2_test.data_contract import aware


def scheduled_competition(event):
    """Return the competition and ordered teams for a single scheduled event."""
    event_id = event['id']
    if not isinstance(event_id, str) or not event_id.strip() or len(event['competitions']) != 1:
        raise ValueError('one identifiable competition required')
    competition = event['competitions'][0]
    if ('id' in competition and str(competition['id']) != event_id
            or 'date' in competition and aware(competition['date']) != aware(event['date'])):
        raise ValueError('conflicting competition identity')
    status = competition.get('status', {}).get('type')
    if status is not None and (status.get('state') != 'pre' or status.get('completed') is not False):
        raise ValueError('conflicting competition status')
    competitors = competition['competitors']
    sides = {side['homeAway']: side['team'] for side in competitors}
    if (len(competitors) != 2 or set(sides) != {'home', 'away'}
            or any(not isinstance(team.get(key), str) or not team[key].strip()
                   for team in sides.values() for key in ('id', 'displayName'))
            or sides['home']['id'] == sides['away']['id']):
        raise ValueError('two distinct identified teams required')
    return competition, sides


def team_match(team, provider_name, cutoff, entries):
    """Return {} for an exact name, the approved alias for a variant, or None."""
    name = team.get('displayName')
    if not name or not provider_name:
        return None
    match = None
    for rule, reviewed_at in entries:
        names = (rule['provider_name'], rule['espn_name'])
        if name not in names and provider_name not in names:
            continue
        if team.get('id') != rule['espn_id'] or name != rule['espn_name']:
            return None
        if provider_name == rule['provider_name'] and aware(reviewed_at) <= aware(cutoff):
            match = dict(rule)
    return {} if provider_name == name else match
