"""Exercise the ablation, time cut and matching of sealed score and total decisions."""
from datetime import date, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

import grade
import history
import study


def sample(shots=None):
    return {"id": "test-fixture", "date": "2024-05-10", "league": "eng.1", "line": 2.5,
            "rates": [1.7, 1.1], "for_against": shots}


FITTED = {"common_volume_coefficients": [0.1, -0.3], "drought_coefficient": 0.4}
UNEVEN = [0, 0, 1, 5, 6, 6]
STEADY = [3, 3, 3, 3, 3, 3]


class HistoricalWindowTest(unittest.TestCase):
    def test_registered_tests_cover_only_the_three_mays(self):
        self.assertEqual([history.blocks(s, "test-inputs") for s in history.TEST],
                         [[date(y, 5, 1)] for y in (2023, 2024, 2025)])
        self.assertGreater(min(history.blocks(s, "test-inputs")[0] for s in history.TEST),
                           max(max(history.blocks(s, "train")) for s in history.TRAIN))

    def test_excludes_unknown_fields_and_same_or_next_day(self):
        cut = date(2020, 5, 1)
        rows = []
        for day in range(10, 24):
            for j in range(6):
                rows.append({"date":date(2020,4,day), "home":f"H{j}", "away":f"A{j}",
                             "goals":(1,0), "sot":(0,2)})
        past = history.make_state(rows, cut).inputs("H0","A0")
        new = rows + [{"date":cut - timedelta(days=1)}, {"date":cut}, {"date":cut + timedelta(days=3)}]
        self.assertEqual(history.make_state(new, cut).inputs("H0","A0"),past)
        self.assertEqual(past["for_against"][0],[6*[0],6*[0]])
        self.assertEqual(past["for_against"][1],[6*[2],6*[2]])

    def test_one_missing_previous_count_cannot_be_replaced_by_seventh(self):
        cutoff = date(2020,5,1)
        rows = [{"date":date(2020,4,day),"home":f"H{j}","away":f"A{j}","goals":(2,1),
                 "sot":(None if (j==0 and day==24) else 2,3)}
                for day in range(10,25) for j in range(5)]
        out = history.make_state(rows,cutoff).inputs("H0","A0")
        self.assertIsNotNone(out["rates"])
        self.assertIsNone(out["for_against"])
        self.assertIsNone(history.make_state(rows,cutoff).inputs("unseen","A0")["rates"])

    def test_score_and_two_sided_line_contract(self):
        self.assertIsNone(history.score({"FTHG":"","FTAG":"0","FTR":""}))
        with self.assertRaisesRegex(ValueError,"inconsistent"):
            history.score({"FTHG":"0","FTAG":"0","FTR":"H"})
        self.assertIsNone(history.historical_line({"B365>2.5":"1.8","B365<2.5":""}))
        self.assertIsNone(history.historical_line({"B365>2.5":"NaN","B365<2.5":"2.0"}))
        self.assertEqual(history.historical_line({"B365>2.5":"1.8","B365<2.5":"2.0"}),2.5)


class ModelTest(unittest.TestCase):
    def test_same_average_distinguishes_drought(self):
        v1,d1 = study.side_inputs([STEADY,STEADY])
        v2,d2 = study.side_inputs([UNEVEN,UNEVEN])
        self.assertEqual(v1,v2)
        self.assertAlmostEqual(d2-d1,0.5)
        with self.assertRaises(ValueError):
            study.side_inputs([STEADY,[None]*6])

    def test_zero_boundary_and_conditional_positive_ratios(self):
        row=sample([[UNEVEN,UNEVEN],[STEADY,STEADY]])
        q0,q1=study.zero_probabilities(row,FITTED)
        a,b=(study.matrix(row["rates"],q) for q in (q0,q1))
        self.assertAlmostEqual(b.sum(),1.)
        self.assertGreater(q1[0],q0[0])
        for axis, q in ((1,q1[0]),(0,q1[1])):
            m,n=a.sum(axis=axis),b.sum(axis=axis)
            self.assertAlmostEqual(n[0],q)
            np.testing.assert_allclose(m[1:]/m[1:].sum(),n[1:]/n[1:].sum(),rtol=2e-14)

    def test_ablation_fallback_and_unforecasted(self):
        row=sample([[STEADY,UNEVEN],[UNEVEN,STEADY]])
        for r, coeffs in [(row,dict(FITTED,drought_coefficient=0)),(sample(),FITTED)]:
            f=study.forecasts([r],coeffs)[0]
            self.assertEqual(f["base"],f["candidate"])
            self.assertEqual(len(set(f["base"]["top"])),3)
        f=study.forecasts([dict(sample(),rates=None)],FITTED)[0]
        self.assertIsNone(f["base"])
        with self.assertRaisesRegex(ValueError,"outcome"):
            study.forecasts([dict(row,score=[0,1])],FITTED)

    def test_outcome_cover_and_missing_line(self):
        forecast=study.forecasts([sample()],FITTED)[0]
        result=grade.grade([forecast],[{"id":"test-fixture","score":[1,0],"line":None}],"historical")
        self.assertEqual(result["overall"]["metrics"]["total_full_win"]["n"],0)
        self.assertEqual(result["overall"]["metrics"]["first"]["n"],1)
        self.assertIsNone(result["overall"]["first_delta_pp_ci95"])
        with self.assertRaisesRegex(ValueError,"differs"):
            grade.grade([forecast],[{"id":"test-fixture","score":[1,0],"line":3.5}],"historical")

    def test_legacy_error_classes_partition_misses(self):
        self.assertEqual(grade.error_path("2-1",[2,1]),"exact")
        self.assertEqual(grade.error_path("0-1",[1,0]),"wrong_blank_status")
        self.assertEqual(grade.error_path("0-1",[0,2]),"positive_counts_only")

    def test_sealed_ids_and_bytes_verified(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            raw,fitfile=root/"input",root/"fit"
            history.write_rows(raw,[sample()])
            study.save(fitfile,dict(FITTED,protocol_sha256=study.file_hash(history.HERE/"protocol.json")))
            study.seal(raw,fitfile,root/"locked","historical")
            forecasts=root/"locked/forecasts.jsonl"
            lock=root/"locked/lock.json"
            study.read_lock(lock,forecasts,"historical")
            forecasts.write_bytes(forecasts.read_bytes()+b"\n")
            with self.assertRaisesRegex(ValueError,"mismatch"):
                study.read_lock(lock,forecasts,"historical")
            with self.assertRaises(FileExistsError):
                study.seal(raw,fitfile,root/"locked","historical")


if __name__ == "__main__":
    unittest.main()
