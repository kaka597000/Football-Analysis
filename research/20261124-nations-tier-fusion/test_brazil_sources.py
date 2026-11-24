import unittest
from unittest.mock import patch

from brazil_sources import score_locked


class RegisteredBrazilPanelTest(unittest.TestCase):
    def test_modified_choices_never_open_result_source(self):
        with patch('brazil_sources.blob', side_effect=AssertionError('labels opened')):
            with self.assertRaisesRegex(ValueError, 'pre-scoring lock'):
                list(score_locked(None, b'{}\n', None))
