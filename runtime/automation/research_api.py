"""Version 1 cross-repository result-review API.

Aliases preserve the existing implementations, signatures, exceptions and queue
persistence. This module is not part of frozen inference source identities.
"""
from automation.collector import ESPN, _ScoreboardQueue as ScoreboardQueue, append, aware, regulation_row
from research_program.three_arm_review import (
    _certify as certify_result,
    _predictions as read_predictions,
    _results as read_results,
)

API_VERSION = 1
__all__ = ['API_VERSION', 'ESPN', 'ScoreboardQueue', 'append', 'aware',
           'regulation_row', 'certify_result', 'read_predictions', 'read_results']
