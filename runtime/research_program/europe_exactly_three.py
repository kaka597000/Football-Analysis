"""Reallocate exactly-three vs four-plus goals within each winning outcome."""

import argparse
from functools import partial
from pathlib import Path

import numpy as np
from scipy.special import expit

from . import all_scope_model as base
from . import europe_three_goal_coverage as replay
from .goal_state_study import sha
from .r8_score_study import read


PROTOCOL = Path(__file__).resolve().parents[2] / 'research/20261126-r37-europe-exactly-three/protocol.json'
EXACT = replay.TOTAL == 3
FOUR_PLUS = replay.TOTAL >= 4
MASKS = {result: ((base.RESULT == result) & EXACT, (base.RESULT == result) & FOUR_PLUS) for result in (0, 2)}


def fit(grids, results):
    outcomes = []
    offsets = []
    for grid, (home, away) in zip(grids, results, strict=True):
        result = base.RESULT[home, away]
        if result == 1 or home + away < 3:
            continue
        exact, wider = [grid[mask].sum() for mask in MASKS[result]]
        offsets.append(np.log(exact / wider))
        outcomes.append(home + away == 3)
    beta = replay.binary_offset(np.asarray(offsets), np.asarray(outcomes))
    return np.array([beta, 0., beta])


def adjust(grid, offsets):
    if not np.any(offsets):
        return grid
    updated = grid.copy()
    for result, (exact, wider) in MASKS.items():
        low, high = grid[exact].sum(), grid[wider].sum()
        share = expit(np.log(low / high) + offsets[result])
        mass = low + high
        updated[exact] *= mass * share / low
        updated[wider] *= mass * (1 - share) / high
    if not np.allclose(replay.groups(updated), replay.groups(grid), rtol=0., atol=2e-12):
        raise ValueError('exact-three calibration moved a locked outcome or total range')
    return updated


def run(data, out):
    if sha(replay.PROTOCOL) != read(PROTOCOL)['r36_protocol_sha256']:
        raise ValueError('the original three-goal comparison differs')
    selector = partial(replay.high_third, target_mask=EXACT)
    forecast = partial(replay.record, calibrate=adjust, select_third=selector)
    replay.run(data, out, protocol=PROTOCOL, fit_offsets=fit, forecast_row=forecast)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.out)
