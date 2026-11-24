import csv
from collections import Counter
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import proxy


def sample(date='04/09/2023', **changed):
    fields = proxy.IDENTITY + proxy.PRICES + proxy.LABELS
    values = dict(zip(fields, ('E0', date, 'Unit Home', 'Unit Away', '2.1', '3.4', '3.6',
                               '1.90', '1.94', '1.88', '1.96', 'never-read', 'never-read')))
    values.update(changed)
    out = StringIO()
    rows = csv.DictWriter(out, fields)
    rows.writeheader()
    rows.writerow(values)
    return out.getvalue().encode()


def inputs(raw):
    return list(proxy.rows_for(raw, 'raw/close/2324_E0.csv', '2324', 'E0', 'inputs'))


class Tests(unittest.TestCase):
    def test_inputs_do_not_project_outcomes_or_invent_clocks(self):
        first = inputs(sample())[0]
        self.assertEqual(inputs(sample(FTHG='200', FTAG='500'))[0], first)
        self.assertTrue({'FTHG', 'FTAG', 'home_goals', 'away_goals', 'score'}.isdisjoint(first))
        self.assertEqual(first['provider'], 'Bet365')
        self.assertTrue(all(first[k] is None for k in ('opening_quoted_at','current_quoted_at','decision_cutoff_at')))

    def test_same_contest_identity_keeps_home_away_orientation(self):
        baseline = inputs(sample())[0]
        changed_price = inputs(sample(**{'B365>2.5':'2.3'}))[0]
        reversed_sides = inputs(sample(HomeTeam='Unit Away', AwayTeam='Unit Home'))[0]
        self.assertEqual(baseline['id'], changed_price['id'])
        self.assertNotEqual(baseline['id'], reversed_sides['id'])
        with self.assertRaisesRegex(ValueError,'invalid fixture identity'):
            inputs(sample(Div='P1'))

    def test_keep_missing_sides_or_incomplete_latest_triplet(self):
        row = inputs(sample(**{'B365>2.5':'', 'B365C>2.5':'nan', 'B365C<2.5':'1', 'B365CA':'n/a'}))[0]
        self.assertEqual(row['opening_total'], dict(line=2.5, over=None, under=1.94))
        self.assertEqual(row['current_total'], dict(line=2.5, over=None, under=None))
        self.assertEqual(row['latest_h2h'], dict(home=2.1, draw=3.4, away=None))
        counts=Counter()
        proxy.completeness(row, counts)
        self.assertEqual([counts[k] for k in ('roster','complete','opening_total_partial','current_total_missing','latest_h2h_partial')],
                         [1,0,1,1,1])

    def test_late_autumn_window_and_gap(self):
        self.assertEqual(inputs(sample('31/07/2023')), [])
        self.assertEqual(inputs(sample('01/12/2023')), [])
        self.assertEqual(inputs(sample('01/09/2024')), [])
        self.assertEqual(len(inputs(sample('01/08/2023'))), 1)
        self.assertEqual(len(inputs(sample('30/11/2023'))), 1)
        self.assertEqual(set(proxy.LEAGUES),{'E0','D1','F1','I1','N1','SP1'})
        self.assertNotIn('2223', proxy.TRAIN + proxy.TEST)

    def test_duplicate_stops_extraction_at_first_source(self):
        sample_rows = sample('04/09/2019', Div='D1').splitlines(keepends=True)
        raw = b''.join(sample_rows + sample_rows[1:])
        leaf = '1920_D1.csv'
        name = f'raw/close/{leaf}'
        identity = sha256(raw).hexdigest()
        manifest = {'files': {name:identity}, 'closing_book_raw_csv': {leaf: {
            'path':name, 'url':'https://www.football-data.co.uk/mmz4281/1920/D1.csv',
            'sha256':identity, 'fetch_time':None}}}
        with tempfile.TemporaryDirectory() as tmp:
            assets=Path(tmp)/'fictional.tar.gz'
            with tarfile.open(assets, 'w:gz') as archive:
                for path, contents in [('ASSET-MANIFEST.json', json.dumps(manifest).encode()),(name, raw)]:
                    member=tarfile.TarInfo(path)
                    member.size=len(contents)
                    archive.addfile(member, BytesIO(contents))
            with patch.object(proxy, 'ARCHIVE_SHA256', sha256(assets.read_bytes()).hexdigest()):
                with self.assertRaisesRegex(ValueError,'duplicate fixture identity'):
                    proxy.build(assets, 'inputs')

    def test_training_projection_ignores_prices_and_preserves_missing_label(self):
        one=list(proxy.rows_for(sample('01/09/2020',FTHG='1',FTAG='2',B365CH='irrelevant'),
                                'training-file','2021','E0','train-labels'))[0]
        self.assertEqual(one['score'],[1,2])
        self.assertNotIn('latest_h2h',one)
        two=list(proxy.rows_for(sample('01/09/2020',FTHG='',FTAG='2'),
                                'training-file','2021','E0','train-labels'))[0]
        self.assertIsNone(two['score'])
        self.assertEqual(two['away_goals'],2)


if __name__ == '__main__':
    unittest.main()
