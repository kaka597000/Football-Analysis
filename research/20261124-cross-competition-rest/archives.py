"""Read fixed, local study packets. Only matched source bytes can supply evidence."""

import hashlib
import json
import tarfile
from collections import defaultdict
from datetime import timezone
from urllib.parse import parse_qs, urlsplit

from research.analysis_freeze import aware, verify

PACKETS = {
    "nov6": "8a2cf5c88b6e9020ffe493c7ddef890925c6e447218cb345042e813195719ff1",
    "nov7": "07c79bc107db14c8113cadab6ed6194bbe946f82cdbea9e2ea61c0a30bd237ac",
    "nov8": "853e649f72b2cab552b37550ad00c5f3526226b55d9458c62894e3ac2a1eb123",
    "nov22": "df67e4209ff042fba2473b770084e84d1401e085f25c8d629616aeaa5b3c72c2",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def utc(s):
    return aware(s).astimezone(timezone.utc)


def output(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    )


class Packet:
    def __init__(self, tag, path):
        self.tag = tag
        with path.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != PACKETS[tag]:
                raise ValueError("Unexpected packet edition")
        self.blobs = {}
        self.members = {}
        with tarfile.open(path, "r|gz") as t:
            for entry in t:
                if not entry.isfile():
                    continue
                k = entry.name
                if k.startswith("objects/") or k.endswith(".json"):
                    raw = t.extractfile(entry).read()
                    h = sha(raw)
                    if k.startswith("objects/") and h != k[8:]:
                        raise ValueError("Corrupt content-addressed member")
                    if k in self.members:
                        raise ValueError("Duplicate member")
                    self.blobs[h] = raw
                    self.members[k] = h
        if tag == "nov22":
            self.members = json.loads(self.blobs[self.members["manifest.json"]])[
                "files"
            ]
        self.refs = defaultdict(list)
        self.pairs = {}
        self.firsts = {}
        for k, h in self.members.items():
            if not k.endswith(".json") or h not in self.blobs:
                continue
            if (
                (
                    k.endswith("/000001.json")
                    and ("/issued-analyses/" in k or "/paired-freezes/" in k)
                )
                or tag == "nov22"
                and k.startswith("freeze-copies/")
            ):
                r = self.json(h)
                if (
                    r.get("revision") != 1
                    or r.get("stage") != "prematch"
                    or r.get("track") not in ("comprehensive", "current-test-pair")
                ):
                    continue
                verify(r)
                if (
                    r["evaluation_slot"] != "FIRST_PREMATCH"
                    or r["previous_sha256"] is not None
                    or r["fixture"]["match_id"] != r["match_id"]
                ):
                    raise ValueError("Invalid first fixture/version")
                store = self.pairs if r["track"] == "current-test-pair" else self.firsts
                if (
                    r["match_id"] in store
                    and store[r["match_id"]]["record_sha256"] != r["record_sha256"]
                ):
                    raise ValueError("Conflicting first issue")
                store[r["match_id"]] = r
            if k.endswith((".meta.json", ".metadata.json")):
                d = self.json(h)
                if isinstance(d, dict) and d.get("http_status", d.get("status")) == 200:
                    self.reference(
                        d.get("url") or d.get("source_url"),
                        d.get("sha256") or d.get("response_sha256"),
                        d.get("query_time") or d.get("query_time_utc"),
                    )
        if tag == "nov22":
            for mid, r in self.firsts.items():
                f = r["fixture"]
                eid = mid.removeprefix("espn:")
                for area in ("west", "other"):
                    k = f"form/{area}/{eid}.json"
                    if k not in self.members:
                        continue
                    form = self.json(self.members[k])
                    if str(form["id"]).removeprefix("espn:") != eid or utc(
                        form["kickoff_at"]
                    ) != utc(f["kickoff_at"]):
                        raise ValueError("Team form packet identity differs")
                    for s in ("home", "away"):
                        side = form[s]
                        v = side.get("team_source") or side
                        if side["team_id"] != f[s]:
                            raise ValueError("Team form side differs")
                        h = v.get("sha256") or v.get("source_sha256")
                        file = v.get("raw_file")
                        if (
                            h
                            and file
                            and self.members.get("form/" + area + "/" + file) == h
                        ):
                            self.reference(
                                v.get("url") or v.get("source_url"),
                                h,
                                form["observed_at"],
                            )
                for s in ("home", "away"):
                    for g in (
                        f.get("recent_league_form", {}).get(s, {}).get("matches", [])
                    ):
                        self.reference(
                            g.get("source_url"),
                            g.get("source_sha256"),
                            g.get("observed_at"),
                        )
                evidence = r["payload"].get("availability", {}).get("raw_source") or {}
                if self.members.get(evidence.get("file")) == evidence.get(
                    "response_sha256"
                ):
                    self.reference(
                        evidence.get("source_url"),
                        evidence.get("response_sha256"),
                        evidence.get("query_time"),
                    )
        for r in self.firsts.values():
            for e in r["fixture"].get("sources", []):
                self.reference(
                    e.get("source_url") or e.get("url"),
                    e.get("response_sha256")
                    or e.get("sha256")
                    or e.get("source_sha256"),
                    e.get("query_time"),
                )

    def json(self, h):
        return json.loads(self.blobs[h])

    def reference(self, url, h, observed):
        if not url or not observed or h not in self.blobs:
            return
        parsed = urlsplit(url)
        q = parse_qs(parsed.query)
        if parsed.scheme != "https":
            return
        if parsed.hostname == "www.fotmob.com":
            if parsed.path in ("/api/data/teams", "/api/teams"):
                kind, key = "team", "id"
            elif parsed.path in ("/api/data/matchDetails", "/api/matchDetails"):
                kind, key = "game", "matchId"
            else:
                return
        elif parsed.hostname == "site.api.espn.com" and parsed.path.endswith(
            "/scoreboard"
        ):
            raw = self.json(h)
            league = parsed.path.split("/")[-2]
            if league not in {r.get("slug") for r in raw.get("leagues", [])}:
                return
            for event in raw.get("events", []):
                id_ = str(event.get("id"))
                ref = (utc(observed), h)
                if ref not in self.refs["espn", id_]:
                    self.refs["espn", id_].append(ref)
            return
        elif parsed.hostname == "site.api.espn.com" and parsed.path.endswith(
            "/summary"
        ):
            kind, key = "espn", "event"
        else:
            return
        ids = q.get(key, [])
        if len(ids) != 1 or not ids[0].isdigit():
            return
        ref = (utc(observed), h)
        if ref not in self.refs[kind, ids[0]]:
            self.refs[kind, ids[0]].append(ref)

    def available(self, kind, native_id, cut):
        return sorted(
            (r for r in self.refs[kind, str(native_id)] if r[0] <= cut), reverse=True
        )


PHASE_RECEIPTS = {
    "ucl-full-20261103/review-20261104/staging/results-structured.json": "a55e5ef60b5a0814e135de1bf311257107b433f0558172d604101f24a68e7713",
    "review-europe-20261105/fotmob-review.json": "96a51897f64d3185382c3ab60d4a8eacb67b17b2cfef00f9cf55007c5dd94e7e",
}


def add_phase_sources(packet, reports):
    for rel, expected in PHASE_RECEIPTS.items():
        path = reports / rel
        raw = path.read_bytes()
        if sha(raw) != expected:
            raise ValueError("Historical cup capture receipt checksum")
        d = json.loads(raw)
        rows = [r["fotmob"] for r in d["records"]] if isinstance(d, dict) else d
        for row in rows:
            s = row["source"]
            content = (path.parent / s["raw_file"]).read_bytes()
            h = s["sha256"]
            if sha(content) != h:
                raise ValueError("Historical cup source checksum")
            packet.blobs[h] = content
            packet.reference(s["url"], h, s["retrieved_at_utc"])


def r7_roster(path):
    if (
        sha(path.read_bytes())
        != "52f1dd07d146a93ba9cec41ce9e1c9cdf75b3db43f8a65da7ffea222467f385d"
    ):
        raise ValueError("R7 immutable edition")
    ids = set()
    keys = set()
    with tarfile.open(path) as t:
        for m in t:
            if not m.isfile() or not m.name.endswith(".json"):
                continue
            r = json.load(t.extractfile(m))
            if r.get("track") != "research-prematch-continuation-20261123":
                continue
            verify(r)
            f = r["fixture"]
            ids.add(f["match_id"])
            keys.add((f["home"], f["away"], utc(f["kickoff_at"])))
    if len(ids) != 192 or len(keys) != 192:
        raise ValueError("R7 roster mismatch")
    return ids, keys


EUROPA_REVIEW_SHA = "8502065c13760f6ebb59dc688e236a3438f8d19d8df73682131b5da27ba6a7ae"


def add_nov5_rounds(packet, archive):
    if sha(archive.read_bytes()) != EUROPA_REVIEW_SHA:
        raise ValueError("Earlier Europa/Conference package checksum")
    prefix = "WCPP/reports/review-uel-20261106/batch-evidence/"
    with tarfile.open(archive) as t:
        sources = json.load(t.extractfile(prefix + "manifest.json"))
        s = next(r for r in sources if r["name"] == "fotmob")
        raw = t.extractfile(prefix + "raw/fotmob.json").read()
    h = sha(raw)
    if (
        s["response_sha256"] != h
        or s["http_status"] != 200
        or s["url"] != "https://www.fotmob.com/api/data/matches?date=20261105"
    ):
        raise ValueError("Daily round response identity")
    doc = json.loads(raw)
    if str(doc["date"]).replace("-", "") != "20261105":
        raise ValueError("Historical round date")
    packet.blobs[h] = raw
    for league in doc["leagues"]:
        for game in league["matches"]:
            packet.refs["daily", str(game["id"])].append((utc(s["query_time_utc"]), h))
