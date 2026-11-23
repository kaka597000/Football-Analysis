"""Current ingestion scope. Retained prediction engines use their versioned registry."""
import registry as retained
from registry import expected_fd, normalize_name

LEAGUES = {
    **retained.LEAGUES,
    'uefa.europa': ('欧联杯', 'UEFA_CLUB', 'cross', None),
    'uefa.europa.conf': ('欧协联', 'UEFA_CLUB', 'cross', None),
    'fifa.world': ('世界杯', 'NATIONAL', 'calendar', None),
}
ALIASES = {
    '欧联杯': 'uefa.europa', '欧联': 'uefa.europa', 'UEFA Europa League': 'uefa.europa',
    '欧协联': 'uefa.europa.conf', 'UEFA Conference League': 'uefa.europa.conf',
    'UEFA Europa Conference League': 'uefa.europa.conf',
    '世界杯': 'fifa.world', 'FIFA World Cup': 'fifa.world',
}
# These competitions follow the same calendar rules as their retained counterparts.
SEASON_CALENDAR = {'uefa.europa': 'uefa.champions', 'uefa.europa.conf': 'uefa.champions',
                   'fifa.world': 'fifa.friendly'}


def competition(value):
    return value if value in LEAGUES else ALIASES.get(value) or retained.competition(value)


def season_for(code, date, declared=None):
    return retained.season_for(SEASON_CALENDAR.get(code, code), date, declared)
