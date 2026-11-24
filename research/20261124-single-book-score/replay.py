"""Reproduce the locked study from the two local Git object stores; no network access."""

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

from inputs import HERE, PROTOCOL


def material(repo, commit, path):
    return subprocess.check_output(["git", "-C", str(repo), "show", f"{commit}:{path}"])


def bundle(data, edition, folder):
    root = "workspace-updates/" + folder + "/"
    manifest = json.loads(material(data, edition, root + "manifest.json"))
    parts = []
    for entry in manifest["ordered_parts"]:
        part = material(data, edition, root + entry["path"])
        if hashlib.sha256(part).hexdigest() != entry["sha256"]:
            raise ValueError("Input part checksum differs: " + folder)
        parts.append(part)
    raw = b"".join(parts)
    if hashlib.sha256(raw).hexdigest() != manifest["archive_sha256"]:
        raise ValueError("Reassembled archive checksum differs: " + folder)
    return raw


def review_files(raw, prefix, dest):
    """Only the indexed ESPN evidence and receipts used by the retained review reader."""
    dest.mkdir()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as t:
        for member in t:
            name = member.name.removeprefix(prefix + "/")
            if name == member.name or not member.isfile() or not name.endswith(".json"):
                continue
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unexpected review member")
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as f:
                f.write(t.extractfile(member).read())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    data, out, repo = args.data.resolve(), args.out.resolve(), HERE.parents[1]
    out.mkdir(parents=True, exist_ok=False)
    dc = PROTOCOL["data_commit"]
    env = dict(os.environ, PYTHONPATH=str(repo / "runtime"), PYTHONDONTWRITEBYTECODE="1",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    def run(name, **kwargs):
        cmd = [sys.executable, str(HERE / name)]
        for opt, value in kwargs.items():
            cmd.extend(["--" + opt, str(value)])
        subprocess.run(cmd, env=env, check=True)

    with TemporaryDirectory(dir=out) as tmp:
        root = Path(tmp)
        reader = root / "reader"
        reader.mkdir()
        for name in ("archives.py", "calendars.py", "prepare.py", "grade.py", "rest_model.py"):
            (reader / name).write_bytes(material(repo, PROTOCOL["archived_replay_reader_commit"],
                "research/20261124-cross-competition-rest/" + name))
        for tag, edition, folder in (
                ("nov6", "e5b3293b85616e496f6e59bbe5a037b133508778", "20261107-night-analysis"),
                ("nov7", "8c60d1489dd788188bac4a2afe8cdfb644761625", "20261107-night19-55matches"),
                ("nov8", "1e1ac4423b31850b1de0c998653c9318e1835d64", "20261108-night19-46matches")):
            (root / (tag + ".tar.gz")).write_bytes(bundle(data, edition, folder))
        archive, r7 = root / "assets.tar.gz", root / "r7.tar.gz"
        archive.write_bytes(material(data, dc, "workspace-updates/20261123-kpi-optimization/source-assets.tar.gz"))
        r7.write_bytes(material(data, dc, "workspace-updates/20261123-prematch-literature/first-issues.tar.gz"))
        history, clocked = out / "history", out / "clocked"
        run("prepare_history.py", archive=archive, out=history)
        run("prepare_clocked.py", reader=reader, packets=root, r7=r7, history=history, out=clocked)

        early, weekend, later = (root / name for name in ("early", "weekend", "later"))
        review_files(bundle(data, dc, "20261107-review-and-aleague"), "WCPP/reports/review-all-20261107", early)
        review_files(bundle(data, dc, "20261108-review67-aus-jpn"),
                     "WCPP/reports/review55-and-aus-jpn-20261108-1343/review-results/raw/espn", weekend)
        later_tar = material(data, dc, "workspace-updates/20261110-review-and-audit/review-20261108/20261110T085236+0800.tar.gz")
        review_files(later_tar, "20261110T085236+0800", later)
        run("settle.py", archive=archive, reader=reader, data=data, early=early, weekend=weekend, later=later,
            history=history, clocked=clocked, out=out / "graded")


if __name__ == "__main__":
    main()
