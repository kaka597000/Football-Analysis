import json
import tempfile
import unittest
from pathlib import Path
from history_process import enrich


class Tests(unittest.TestCase):
    def test_reconciles_provider_id_date_score_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'page.json'
            raw={'id':17,'kickoff_utc':'2020-01-01T12:00:00Z','goals':{'home':1,'away':0},
                 'statistics':{k:{'home':a,'away':b} for k,a,b in [('shots_on_target',4,2),('shots_off_target',6,3),('possession',60,40)]}}
            p.write_text(json.dumps({'response':{'data':[raw]}}))
            rows=[{'date':'2020-01-01','home_goals':1,'away_goals':0,'source_url':'https://example.test/fixture',
                   'api_evidence':{'source_path':str(p),'source_key':'5dollar:17','query_time':'2021-01-01T00:00:00Z'}}]
            result,audit=enrich(rows,d)
            self.assertEqual(audit['matched_process_rows'],1);self.assertNotIn('process',rows[0])
            self.assertEqual(result[0]['process']['home']['shots_on_target'],4)
            rows[0]['home_goals']=2
            with self.assertRaises(ValueError):enrich(rows,d)

    def test_rejects_unapproved_archive_path(self):
        rows=[{'api_evidence':{'source_path':'/etc/passwd','source_key':'5dollar:17'}}]
        with self.assertRaises(ValueError):enrich(rows,'/tmp/only-archives')


if __name__=='__main__':unittest.main()
