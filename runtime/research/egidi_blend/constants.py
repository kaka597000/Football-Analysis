"""Fixed research scope. OOD leagues stay out of blend and ranker training."""
import sys
from dataclasses import dataclass
from pathlib import Path

_RUNTIME = Path(__file__).resolve().parents[2]
if str(_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_RUNTIME))

from league_tracks import EUROPE_IN_DOMAIN, OOD_ASIA, OOD_MLS, OOD_RESEARCH_TRACKS


VERSION = 'WCPP-EGIDI-BLEND-0.1'
# Iteration order is part of the published research candidate. Membership must
# stay the Big Five; do not append aus.1, jpn.1, kor.1 or usa.1 here.
EUROPE = ('eng.1', 'esp.1', 'ita.1', 'ger.1', 'fra.1')
if set(EUROPE) != set(EUROPE_IN_DOMAIN):
    raise RuntimeError('Europe training tuple drifted from the in-domain Big Five')
ALL_LEAGUES = EUROPE + OOD_RESEARCH_TRACKS

# football-data.co.uk division codes already used by runtime/database/providers.py.
# This module does not call ESPN and does not invent a Korea scoreboard slug.
FOOTBALL_DATA_DIVISIONS = {
    'eng.1': 'E0',
    'esp.1': 'SP1',
    'ita.1': 'I1',
    'ger.1': 'D1',
    'fra.1': 'F1',
}

# Era cuts are pre-registered. They are not chosen from the test season.
ERA_STARTS = (
    (2022, '2022_plus'),
    (2016, '2016_2021'),
    (2008, '2008_2015'),
    (0, 'pre_2008'),
)


def era_of(season_start):
    for start, name in ERA_STARTS:
        if season_start >= start:
            return name
    return 'pre_2008'


def season_start_for(league, date):
    """Season key aligned with runtime/database/registry.py for these leagues.

    Cross-year leagues (Europe top five and A-League) roll in July.
    K League and MLS use the calendar year, matching registry kind "calendar".
    J1 is a calendar year through 2025. January–June 2026 J1 is the registry's
    unclassified transition and is excluded (returns None) so it is not merged
    into 2026/27.
    """
    year = int(date[:4])
    month = int(date[5:7])
    if league == 'jpn.1':
        if year <= 2025:
            return year
        if year == 2026 and month < 7:
            return None
        return year if month >= 7 else year - 1
    if league == 'kor.1' or league in OOD_MLS:
        return year
    if league in EUROPE or league == 'aus.1':
        return year if month >= 7 else year - 1
    raise ValueError('unsupported league for this research track: ' + league)


@dataclass
class Config:
    half_life_days: float = 730.0
    min_train_matches: int = 80
    min_prior_seasons: int = 3
    min_weight_matches: int = 40
    min_ranker_groups: int = 30
    cap: int = 10
    ridge: float = 6.0
    rate_clip: tuple = (0.05, 5.0)
    share_grid: tuple = (0.0, 0.04, 0.10)
    rho_grid: tuple = (-0.08, -0.04, -0.015, 0.0)
    pi_grid: tuple = (0.0, 0.03, 0.07)
    weight_grid_1x2: tuple = (0.0, 0.25, 0.5, 0.75, 1.0)
    weight_grid_ou: tuple = (0.0, 0.5, 1.0)
    weight_grid_cs: tuple = (0.0, 1.0)
    top_k: int = 10
    ranker_rounds: int = 60
    ranker_leaves: int = 15
    ranker_min_data: int = 20
    ranker_learning_rate: float = 0.08
    seed: int = 20261101

    def tiny(self):
        """Small grids for unit tests. Not the published backtest settings."""
        self.min_train_matches = 24
        self.min_prior_seasons = 3
        self.min_weight_matches = 5
        self.min_ranker_groups = 4
        self.cap = 6
        self.share_grid = (0.0, 0.08)
        self.rho_grid = (-0.04, 0.0)
        self.pi_grid = (0.0, 0.05)
        self.weight_grid_1x2 = (0.0, 1.0)
        self.weight_grid_ou = (0.0, 1.0)
        self.weight_grid_cs = (0.0,)
        self.ranker_rounds = 8
        self.ranker_leaves = 4
        self.ranker_min_data = 2
        return self
