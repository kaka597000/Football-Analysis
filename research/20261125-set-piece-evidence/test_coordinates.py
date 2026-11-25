import json
import unittest

from coverage_ledger import summarize
from located import located_plays, page_detail


def shot(kind="FromCorner", x=91, y=38, xg=0.12, own=False, side="11"):
    return dict(situation=kind, x=x, y=y, expectedGoals=xg, isOwnGoal=own, teamId=side)


class LocationTests(unittest.TestCase):
    def test_native_set_play_counts_and_quality(self):
        kinds = ["FromCorner", "SetPiece", "FreeKick", "ThrowInSetPiece"]
        s = [shot(kind=k) for k in kinds] + [shot(side="12")]
        r = located_plays(s, ("11", "12"))
        self.assertEqual(
            r["set_plays_by_team"],
            {"11": dict.fromkeys(kinds, 1), "12": {"FromCorner": 1}},
        )
        self.assertEqual(r["set_play_xg_by_team"]["12"]["FromCorner"], 0.12)

    def test_true_zero_only_from_validated_attempts(self):
        s = [shot("RegularPlay", x=None), shot("Penalty"), shot(own=True)]
        self.assertEqual(
            located_plays(s, ("11", "12"))["set_plays_by_team"], {"11": {}, "12": {}}
        )

    def test_bad_xy_rejects_entire_slot(self):
        for axis, value in [
            ("x", None),
            ("x", True),
            ("x", 106),
            ("y", 69),
            ("y", -0.1),
            ("y", float("nan")),
        ]:
            with (
                self.subTest(axis=axis, value=value),
                self.assertRaisesRegex(ValueError, "location"),
            ):
                located_plays([shot(), shot(**{axis: value})], ("11", "12"))

    def test_available_and_unavailable_are_paired_to_the_current_miss(self):
        result = summarize(
            [
                ("sample", "Current", "a", "1-0", True, False),
                ("sample", "Current", "b", "3-1", True, True),
                ("sample", "Current", "c", "3-2", False, True),
            ]
        )["sample/Current"]
        self.assertEqual(
            result["all"],
            {
                "targets": 3,
                "complete": 2,
                "misses": 2,
                "complete_misses": 1,
                "unavailable_misses": 1,
            },
        )
        self.assertEqual(result["0-2"]["unavailable_misses"], 1)
        self.assertEqual(result["3"]["targets"], 0)
        self.assertEqual(result["4+"]["complete_misses"], 1)

    def test_page_uses_one_native_payload(self):
        data = {"general": {}, "header": {}, "content": {}}
        block = (
            '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": data}})
            + "</script>"
        )
        self.assertEqual(page_detail(block.encode()), data)
        for bad in (
            "",
            block + block,
            block.replace("</script>", ""),
            block.replace('"header"', '"other"'),
        ):
            with self.subTest(bad=bad[:25]), self.assertRaises(ValueError):
                page_detail(bad.encode())


if __name__ == "__main__":
    unittest.main()
