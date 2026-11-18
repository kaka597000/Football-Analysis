"""Independent actual-line decision diagnostics; never rewrites a frozen direction.

Full-win accuracy, positive settlement probability and expected monetary return
are different objectives on Asian quarter lines. Compare them on the same matrix
and quote before deciding which policy to freeze prospectively.
"""
import math

from v2.model import total_settlement
from research_program.future_validation import check_matrix, quote_audit


OBJECTIVES = ('settlement_balance', 'strict_full_win', 'positive_settlement', 'expected_return')


def decisions(prediction):
    """Return all predeclared choices, retaining unavailable quotes as missing."""
    matrix = check_matrix(prediction)
    audit = quote_audit(prediction['input'])
    result = {'quote_audit': audit, 'choices': {}, 'probabilities': {}, 'objectives': {},
              'active_model_changed': False, 'scope': 'INDEPENDENT_POLICY_DIAGNOSTIC'}
    if not audit['eligible']:
        return result
    quote = audit['quote']
    probabilities = {side: total_settlement(matrix, quote['line'], side)
                     for side in ('over', 'under')}
    values = {name: {} for name in OBJECTIVES}
    for side, p in probabilities.items():
        win_units = p['win'] + .5 * p['half_win']
        loss_units = p['loss'] + .5 * p['half_loss']
        values['settlement_balance'][side] = win_units - loss_units
        values['strict_full_win'][side] = p['win'] / (1 - p['push']) if p['push'] < 1 else 0.
        values['positive_settlement'][side] = p['win'] + p['half_win']
        values['expected_return'][side] = (quote[side + '_odds'] - 1) * win_units - loss_units
    baseline = max(values['settlement_balance'], key=values['settlement_balance'].get)
    for name, objective in values.items():
        # Numerically tied objectives retain the original balance choice.
        result['choices'][name] = baseline if math.isclose(objective['over'], objective['under'], abs_tol=1e-12) else max(objective, key=objective.get)
    result.update(probabilities=probabilities, objectives=values, quote=quote,
                  original_frozen_direction=(prediction.get('total') or {}).get('reference_direction'))
    return result
