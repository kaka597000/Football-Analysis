"""League-specific attack and defence from the opponents and prices of past matches."""

from datetime import date

import numpy as np
from scipy.linalg import solve

from research.analysis_freeze import aware, digest
from .future_validation import quote_audit
from . import kpi_score_candidate as form
from .kpi_score_candidate import LEAGUES, artifact_identity
from .market_baseline import predict as market_predict, quote_only_matrices
from .score_form import poisson_matrix, summarize
from .three_arm_model import score_picks, top_three_scores

VERSION = 'WCPP-PRIOR-TEAM-STRENGTH-1'
ARMS = ('prior_goals', 'prior_market', 'prior_market_xg')
VENUE = np.array([1.50, 1.20])
HALF_LIFE_DAYS = 120
TEAM_RIDGE, VENUE_RIDGE = 3., 20.
RATE_BOUNDS = (.08, 6.)
SHAPE = (31, 31)


def observations(goals, market=None, xg=None):
    """Each column is an alternative target from the same *completed* fixture."""
    goals = np.asarray(goals, dtype=float)
    market = goals if market is None else np.asarray(market, dtype=float)
    xg = goals if xg is None else np.asarray(xg, dtype=float)
    targets = np.column_stack((goals, market, .8 * market + .2 * xg))
    if targets.shape != (2, len(ARMS)) or not np.isfinite(targets).all() or targets.min() < 0:
        raise ValueError('two finite nonnegative goal observations required per source')
    return targets


class LeagueStrength:
    """Decayed normal equations shared by the three attack/defence regressions."""

    def __init__(self):
        self.teams = {}
        self.last_seen = {}
        self.last_day = None
        self.gram = np.zeros((2, 2))
        self.rhs = np.zeros((2, len(ARMS)))

    def _decay(self, day):
        if self.last_day is None:
            return 1.
        days = (day - self.last_day).days
        if days < 0:
            raise ValueError('team strength cannot rewind its history')
        return .5 ** (days / HALF_LIFE_DAYS)

    def _indices(self, role, attacker, defender):
        indices = [role]
        if attacker in self.teams:
            indices.append(2 + 2 * self.teams[attacker])
        if defender in self.teams:
            indices.append(3 + 2 * self.teams[defender])
        return indices

    def update_day(self, day, games):
        """Apply all completed fixtures on one date after that day's predictions."""
        if self.last_day is not None and day <= self.last_day:
            raise ValueError('updates must use a new match date')
        games = list(games)
        if not games:
            return
        for home, away, targets in games:
            if not home or not away or home == away:
                raise ValueError('distinct identified teams required')
            if targets.shape != (2, len(ARMS)) or not np.isfinite(targets).all() or (targets < 0).any():
                raise ValueError('invalid targets for team-strength update')
        decay = self._decay(day)
        self.gram *= decay
        self.rhs *= decay
        for home, away, _ in games:
            for team in (home, away):
                self.teams.setdefault(team, len(self.teams))
                self.last_seen[team] = day.isoformat()
        growth = 2 + len(self.teams) * 2 - len(self.gram)
        if growth:
            self.gram = np.pad(self.gram, ((0, growth), (0, growth)))
            self.rhs = np.pad(self.rhs, ((0, growth), (0, 0)))
        for home, away, targets in games:
            for role, (attacker, defender) in enumerate(((home, away), (away, home))):
                ix = self._indices(role, attacker, defender)
                self.gram[np.ix_(ix, ix)] += 1.
                self.rhs[ix] += targets[role] - VENUE[role]
        self.last_day = day

    def coefficients(self, day):
        if self.last_day is not None and day <= self.last_day:
            raise ValueError('forecast requires strictly previous match dates')
        weight = self._decay(day)
        gram = self.gram * weight
        ridge = np.full(len(gram), TEAM_RIDGE)
        ridge[:2] = VENUE_RIDGE
        gram.flat[::len(gram) + 1] += ridge
        return solve(gram, self.rhs * weight, assume_a='pos')

    def rates(self, coefficients, home, away):
        sides = ((home, away), (away, home))
        return np.stack([VENUE[role] + coefficients[self._indices(role, *teams)].sum(axis=0)
                         for role, teams in enumerate(sides)]).clip(*RATE_BOUNDS)

    def snapshot(self):
        return {'teams': list(self.teams), 'last_seen': dict(self.last_seen),
                'through': self.last_day.isoformat() if self.last_day else None,
                'gram': self.gram.tolist(), 'rhs': self.rhs.tolist()}

    @classmethod
    def restore(cls, payload):
        state = cls()
        state.teams = {team: i for i, team in enumerate(payload['teams'])}
        state.last_seen = dict(payload['last_seen'])
        state.last_day = date.fromisoformat(payload['through']) if payload['through'] else None
        state.gram = np.array(payload['gram'])
        state.rhs = np.array(payload['rhs'])
        return state


def selections(matrix, line):
    picks = score_picks(matrix, line=line)
    wdl = picks['wdl_probabilities']
    picks['wdl'] = next(side for side, p in wdl.items() if p >= max(wdl.values())-1e-12)
    return dict(picks, top3=top_three_scores(matrix))


def allocate_picks(incumbent, team, policy):
    """Keep the selected first score and fill the two distinct backup slots."""
    incumbent = incumbent or team
    by_name = {'six_match_xg': incumbent, 'strength': team}
    scores = policy['scores']
    first = team if scores == 'strength' else incumbent
    backups = incumbent if scores == 'six_match_xg' else team
    primary = first['top3'][0]
    result = {'top3': [primary, *[s for s in backups['top3'] if s != primary][:2]], 'primary': primary}
    if 'primary_probability' in first:
        result['primary_probability'] = first['primary_probability']
    for head, keys in (('wdl', ('wdl', 'wdl_probabilities')), ('total', ('total', 'total_side'))):
        expert = by_name[policy[head]]
        result.update({k: expert[k] for k in keys if k in expert})
    return result


def six_match_matrix(artifact, cohort, home, away, day):
    recent = artifact['recent'].get(cohort, {})
    sides = [summarize(list(reversed(recent.get(t, []))), day) for t in (home, away)]
    if not all(s['n'] for s in sides):
        return None
    x = form.form_features(cohort, *sides)[None]
    arm = 'form_xg' if all(s['xg_n'] for s in sides) else 'form_goals'
    if arm == 'form_goals':
        form.mask_xg(x)
    return poisson_matrix(form.expected_form(artifact['six_match'][arm], x)[0], SHAPE)


def predict(artifact, fixture):
    if artifact['version'] != VERSION or artifact_identity(artifact) != artifact.get('artifact_sha256'):
        raise ValueError('prior strength artifact identity mismatch')
    cohort = fixture['cohort']
    base = {'version': VERSION, 'artifact_sha256': artifact['artifact_sha256']}
    if (cohort not in LEAGUES or fixture.get('stage') != 'regular' or fixture.get('neutral') is not False
            or fixture.get('score_basis') != 'regulation_90'):
        return dict(base, status='ABSTAIN_OUTSIDE_TRAINED_SCOPE')
    cutoff, kickoff = (aware(fixture[k]) for k in ('cutoff_at', 'kickoff_at'))
    if not aware(artifact['built_at']) <= cutoff < kickoff or artifact['selection_end'] >= kickoff.date().isoformat():
        raise ValueError('inconsistent fit, issue or target date')
    state = LeagueStrength.restore(artifact['states'][cohort])
    if state.last_day and state.last_day > cutoff.date():
        raise ValueError('team-strength evidence postdates cutoff')
    crosswalk = artifact['team_map'].get(cohort, {})
    home, away = (crosswalk.get(fixture[role]) for role in ('home', 'away'))
    if not home or not away or home == away:
        return dict(base, status='ABSTAIN_UNMAPPED_TEAMS')
    rates = state.rates(state.coefficients(kickoff.date()), home, away)
    arm = artifact['selected_strength']
    matrix = poisson_matrix(rates[:, ARMS.index(arm)], SHAPE)
    audit = quote_audit(fixture)
    line = audit['quote']['line'] if audit['eligible'] else None
    market = market_predict(fixture)
    team_pick = selections(matrix, line)
    components = {'strength': team_pick,
                  'prior_goals': selections(poisson_matrix(rates[:, ARMS.index('prior_goals')], SHAPE), line)}
    picks = team_pick
    path, matrix_role = arm, arm
    if artifact.get('heads'):
        six = six_match_matrix(artifact, cohort, home, away, kickoff.date())
        six_pick = selections(six, line) if six is not None else None
        components['six_match_xg'] = six_pick
        picks = allocate_picks(six_pick, team_pick, artifact['heads'])
        path, matrix_role = 'independent_selections', 'strength_component'
    if market['status'] == 'AVAILABLE':
        snap = market['quote_snapshot']
        _, full = quote_only_matrices(snap['h2h']['prices'],
                                      dict(snap['total']['prices'], line=snap['total']['line']))
        current = full[:SHAPE[0], :SHAPE[1]]
        current = current / current.sum()
        if artifact['use_market_mix']:
            matrix = .75 * current + .25 * matrix
            path = 'market_plus_strength'
        else:
            matrix, path = current, 'market'
        picks, matrix_role = selections(matrix, line), path
    return dict(base, **picks, status='AVAILABLE', effective_recipe=path, matrix_role=matrix_role,
                matched_teams=[home, away], last_team_dates=[state.last_seen.get(t) for t in (home, away)],
                strength_rates=rates[:, ARMS.index(arm)].tolist(),
                components=components, quote_status=market['status'],
                matrix=matrix.tolist(), matrix_sha256=digest(matrix.tolist()))
