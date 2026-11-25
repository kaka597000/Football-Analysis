"""R22 score-grid projection preserving the signed R7 draw cells and WDL masses."""

import math

import numpy as np

from . import all_scope_model as base


VERSION = 'R22-five-league-draw-preserving-1'
DOSES = (0.0, 0.5, 1.0)
FORECASTS = ('candidate', 'projected_r21_dose', 'unprotected_matched')


def _grid(value, name):
    grid = np.asarray(value, dtype=float)
    if (grid.ndim != 2 or grid.shape != base.H.shape or not grid.size
            or not np.isfinite(grid).all() or (grid < 0).any()
            or not math.isclose(float(grid.sum()), 1.0, rel_tol=1e-9, abs_tol=1e-12)):
        raise ValueError(name + ' must be a finite normalized nonnegative score grid')
    return grid


def _dose(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value not in DOSES:
        raise ValueError('dose must be one of 0, 0.5, or 1')
    return float(value)


def project(prior, full):
    """Project a full contextual grid onto the original R7 draw cells and WDL masses.

    Draw cells are copied exactly. Home-win and away-win blocks are independently
    rescaled to their original R7 mass. If a contextual block has zero mass, its
    within-block probabilities inherit the R7 prior.
    """
    prior = _grid(prior, 'prior')
    full = _grid(full, 'full')
    if prior.shape != full.shape:
        raise ValueError('prior and full grids must have identical shapes')
    output = np.zeros_like(prior)
    masks = (base.H > base.A, base.H == base.A, base.H < base.A)
    for i, mask in enumerate(masks):
        target_mass = float(prior[mask].sum())
        if i == 1:
            output[mask] = prior[mask]
            continue
        source_mass = float(full[mask].sum())
        if target_mass == 0.0:
            continue
        if source_mass > 0.0:
            output[mask] = (full[mask] / source_mass) * target_mass
        else:
            output[mask] = prior[mask]
    # Recheck the invariants here so roundoff or an unexpected result mask cannot
    # silently alter the signed prior.
    if not np.isfinite(output).all() or (output < 0).any() or not math.isclose(
            float(output.sum()), 1.0, rel_tol=1e-10, abs_tol=1e-12):
        raise ArithmeticError('projected grid is not a normalized probability distribution')
    if not np.array_equal(output[base.H == base.A], prior[base.H == base.A]):
        raise ArithmeticError('draw cells changed during projection')
    for mask in masks:
        if not math.isclose(float(output[mask].sum()), float(prior[mask].sum()),
                            rel_tol=1e-10, abs_tol=1e-12):
            raise ArithmeticError('WDL block mass changed during projection')
    return output


def _mixture(prior, full, dose):
    return prior.copy() if dose == 0.0 else (1.0 - dose) * prior + dose * full


def _forecast(grid, original, line, *, inherit_score=False):
    if inherit_score:
        score = dict(primary=original['primary'], top3=list(original['top3']))
    else:
        score = base.pick(grid, line)
    # R22 changes only the exact-score distribution. The original R7 WDL and
    # actual-line total decisions stay fixed at every dose.
    return dict(primary=score['primary'], top3=list(score['top3']),
                wdl=original['wdl'], total_side=original.get('total_side'),
                total_line=original.get('total_line', line))


def choices(prior, full, chosen_dose, r21_dose, r7_original, line):
    """Return three forecasts and their score grids under the R22 protocol."""
    prior = _grid(prior, 'prior')
    full = _grid(full, 'full')
    if prior.shape != full.shape:
        raise ValueError('prior and full grids must have identical shapes')
    chosen_dose, r21_dose = _dose(chosen_dose), _dose(r21_dose)
    if (not isinstance(r7_original, dict) or r7_original.get('wdl') not in base.SIDES
            or not isinstance(r7_original.get('primary'), str)
            or not isinstance(r7_original.get('top3'), list) or len(r7_original['top3']) != 3
            or len(set(r7_original['top3'])) != 3 or r7_original['primary'] != r7_original['top3'][0]):
        raise ValueError('a valid original R7 first, ordered three, and WDL are required')
    if r7_original.get('total_side') not in (None, 'over', 'under'):
        raise ValueError('original R7 total side must be over, under, or None')
    if r7_original.get('total_side') is not None and r7_original.get('total_line') != line:
        raise ValueError('original R7 total line differs from the supplied common line')
    if line is not None and (type(line) not in (int, float) or not math.isfinite(line) or line <= 0):
        raise ValueError('line must be a positive finite number or None')

    protected = project(prior, full)
    grids = {
        'candidate': _mixture(prior, protected, chosen_dose),
        'projected_r21_dose': _mixture(prior, protected, r21_dose),
        'unprotected_matched': _mixture(prior, full, chosen_dose),
    }
    forecasts = {
        name: _forecast(grid, r7_original, line, inherit_score=(dose == 0.0))
        for (name, grid), dose in zip(grids.items(), (chosen_dose, r21_dose, chosen_dose), strict=True)
    }
    return forecasts, grids
