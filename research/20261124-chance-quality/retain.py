"""Keep a copy of bytes already received by an authorized normal reader. No network I/O."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from intake import valid_receipt
from opportunities import clock, require


def publish_once(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(body)
        tmp.flush()
        os.fsync(tmp.fileno())
    try:
        try:
            os.link(tmp.name, path)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(tmp.name)


def now():
    return datetime.now(timezone.utc)


def retain(root, body, receipt, match_id, cutoff):
    url = receipt["url"]
    require(
        valid_receipt({url: receipt}, url, match_id, cutoff) is not None,
        "unusable_receipt",
    )
    h = sha256(body).hexdigest()
    require(h == receipt["sha256"], "raw_digest")
    identity = dict(
        url=url,
        requested_at=receipt["requested_at"],
        received_at=receipt["received_at"],
        http_status=receipt["http_status"],
        sha256=h,
        match_id=str(match_id),
    )
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    proof = root / "receipts" / (sha256(encoded).hexdigest() + ".json")
    obj = root / "objects" / (h + ".bin")
    if not proof.exists():
        require(
            clock(receipt["received_at"]) <= now() <= cutoff, "late_first_retention"
        )
        publish_once(obj, body)
        require(sha256(obj.read_bytes()).hexdigest() == h, "stored_bytes_disagree")
        kept = now()
        require(clock(receipt["received_at"]) <= kept <= cutoff, "late_first_retention")
        publish_once(
            proof,
            json.dumps(
                dict(receipt=identity, local_verified_at=kept.isoformat()),
                sort_keys=True,
            ).encode(),
        )
    prior = json.loads(proof.read_bytes())
    require(
        prior["receipt"] == identity
        and clock(receipt["received_at"]) <= clock(prior["local_verified_at"]) <= cutoff
        and obj.is_file()
        and sha256(obj.read_bytes()).hexdigest() == h,
        "retained_proof_incomplete",
    )
    return proof


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw", required=True, type=Path)
    p.add_argument("--receipt", required=True, type=Path)
    p.add_argument("--match-id", required=True)
    p.add_argument("--cutoff", required=True)
    p.add_argument("--private-dir", required=True, type=Path)
    a = p.parse_args()
    proof = retain(
        a.private_dir,
        a.raw.read_bytes(),
        json.loads(a.receipt.read_bytes()),
        a.match_id,
        clock(a.cutoff),
    )
    print(proof.name)
