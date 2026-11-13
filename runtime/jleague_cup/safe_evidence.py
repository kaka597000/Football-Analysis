"""Additional admission checks without changing hash-pinned evidence engines."""
import re

from . import evidence


LABEL_FIELDS = frozenset({
    'home_goals', 'away_goals', 'actual', 'result', 'outcome', 'h90', 'a90', 'home_90',
    'away_90', 'home_et', 'away_et', 'home_pen', 'away_pen', 'home_penalties',
    'away_penalties', 'home_total', 'away_total', 'home_score', 'away_score',
    'score', 'scores', 'final_score', 'score90', 'ht_score', 'ft_score',
    'regulation_score', 'actual_score', 'scoreline', 'goals', 'home_full_time', 'away_full_time',
    'home_score_90', 'away_score_90', 'home_goals_90', 'away_goals_90',
    'home_regulation_goals', 'away_regulation_goals',
})
_LABEL_KEYS = {re.sub(r'[^a-z0-9]', '', key.lower()) for key in LABEL_FIELDS}


def validate_target(fixture):
    """Historical first_leg_record labels are permitted; target labels are not."""
    if not isinstance(fixture, dict):
        raise ValueError('fixture object required')
    for key in fixture:
        normalized = re.sub(r'[^a-z0-9]', '', str(key).lower())
        if normalized in _LABEL_KEYS:
            raise ValueError('target labels prohibited: ' + str(key))
    for side in ('home', 'away'):
        if not isinstance(fixture.get(side), str) or not fixture[side].strip():
            raise ValueError('explicit team identity required')
    if fixture['home'] == fixture['away']:
        raise ValueError('different teams required')


def verify_row(row):
    fact = evidence.verify_row(row)
    display = fact['display_score']
    if re.fullmatch(r'\d+:\d+', display):
        return fact
    shown = re.fullmatch(r'(\d+):(\d+)\s+(AfE|AET|AfP)', display)
    if not shown:
        raise ValueError('explicit completed listing score/status required')
    raw, _ = evidence.source(row['period_evidence'])

    def field(name):
        values = re.findall(r'id="' + name + r'"[^>]*>(.*?)</', raw, re.S)
        if len(values) != 1:
            raise ValueError('unique detail field required: ' + name)
        return evidence.clean(values[0])

    score = re.fullmatch(r'(\d+):(\d+)', field('js-score'))
    expected_status = 'After Penalties' if shown[3] == 'AfP' else 'After Extra Time'
    if (score is None or tuple(map(int, score.groups())) != tuple(map(int, shown.groups()[:2]))
            or field('js-eventstage') != expected_status):
        raise ValueError('listing/detail completed result or status conflict')
    return fact
