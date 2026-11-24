"""One-shot, on-demand receipt of public Understat league pages. Output is local only."""

import argparse
import gzip
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

LEAGUES = {
    "eng.1": "EPL",
    "esp.1": "La liga",
    "ger.1": "Bundesliga",
    "ita.1": "Serie A",
    "fra.1": "Ligue 1",
}
BASE = "https://understat.com"
AGENT = "WCPP-local-research/1.0"


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


class SameOrigin(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if (urlsplit(newurl).scheme, urlsplit(newurl).netloc) != (
            "https",
            "understat.com",
        ):
            raise ValueError("Source redirected outside the approved host")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request(opener, url):
    # Identical public resource as on the native league page, using an identified client.
    req = Request(
        url,
        headers={
            "User-Agent": AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with opener.open(req, timeout=20) as response:
            body = response.read(8_000_001)
            if len(body) > 8_000_000:
                raise ValueError("Source exceeded the bounded read size")
            return response.status, body, response.headers
    except HTTPError as ex:
        return ex.code, b"", ex.headers


def insert(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        name = tmp.name
    try:
        try:
            os.link(name, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("A prior receipt at this identity differs")
    finally:
        os.unlink(name)


def normalize_bytes(body, encoding):
    return (
        gzip.decompress(body) if encoding == "gzip" or body[:2] == b"\x1f\x8b" else body
    )


def record(root, fields, raw=None):
    if raw is not None:
        h = digest(raw)
        fields["wire_sha256"] = h
        fields["raw"] = "objects/" + h + ".bin"
        insert(root / fields["raw"], raw)
    body = (
        json.dumps(fields, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    ).encode()
    insert(root / "receipts" / (digest(body) + ".json"), body)


def parse_policy(status, body, encoding):
    rp = RobotFileParser()
    if status == 404:
        rp.parse([])
        return rp
    if status != 200:
        return None
    text = normalize_bytes(body, encoding).decode("utf-8-sig")
    if text.lstrip().startswith("<"):
        return None
    rp.parse(text.splitlines())
    return rp


def capture(root, year, codes):
    opener = build_opener(SameOrigin())
    robots = BASE + "/robots.txt"
    started = now()
    try:
        status, body, hdr = request(opener, robots)
        rp = parse_policy(status, body, hdr.get("Content-Encoding"))
        if rp is None:
            record(
                root,
                dict(
                    kind="source_policy",
                    source=robots,
                    start_at=started,
                    received_at=now(),
                    status=status,
                    robots_sha256=digest(body),
                    state="POLICY_UNAVAILABLE",
                ),
            )
            return
    except (URLError, OSError, ValueError) as ex:
        record(
            root,
            dict(
                kind="source_policy",
                source=robots,
                start_at=started,
                received_at=now(),
                state="POLICY_UNAVAILABLE",
                error_type=type(ex).__name__,
            ),
        )
        return
    record(
        root,
        dict(
            kind="source_policy",
            source=robots,
            start_at=started,
            received_at=now(),
            status=status,
            state="READ",
            robots_sha256=digest(body),
        ),
    )
    for n, code in enumerate(codes):
        url = BASE + "/getLeagueData/" + quote(LEAGUES[code], safe="") + "/" + str(year)
        receipt = dict(
            kind="league",
            source=url,
            competition=code,
            season=year,
            start_at=now(),
            clock_basis="local_utc_after_response_read",
            provider_row_published_at=None,
        )
        if not rp.can_fetch(AGENT, url):
            record(
                root, receipt | dict(received_at=now(), state="DISALLOWED_BY_ROBOTS")
            )
            continue
        if n:
            time.sleep(1)
        try:
            status, wire, hdr = request(opener, url)
            receipt.update(
                status=status, received_at=now(), server_date=hdr.get("Date")
            )
            if status != 200:
                record(root, receipt | dict(state="HTTP_UNAVAILABLE"))
                if status in (401, 403, 429):
                    break
                continue
            decoded = normalize_bytes(wire, hdr.get("Content-Encoding"))
            receipt["decoded_sha256"] = digest(decoded)
            try:
                o = json.loads(decoded)
            except (UnicodeError, ValueError):
                o = None
            if not (
                isinstance(o, dict)
                and isinstance(o.get("dates"), list)
                and isinstance(o.get("teams"), dict)
            ):
                record(
                    root,
                    receipt
                    | dict(state="NO_NATIVE_LEAGUE_DATA", response_sha256=digest(wire)),
                )
                break
            record(root, receipt | dict(state="NATIVE_LEAGUE_DATA"), wire)
        except (URLError, OSError, ValueError) as ex:
            record(
                root,
                receipt
                | dict(
                    received_at=now(), state="READ_ERROR", error_type=type(ex).__name__
                ),
            )
            break


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--competition", nargs="+", choices=LEAGUES, required=True)
    a = p.parse_args()
    capture(a.out, a.season, list(dict.fromkeys(a.competition)))
