"""Fit on four past seasons and seal historical development matrices without target labels."""

import argparse
import numpy as np
from pathlib import Path
from inputs import HERE, PROTOCOL, PROTOCOL_SHA, dump, identities, read_csvs, sha, tier_counts, wdl
from research_program.single_book_margin import fit_weight, paired_matrices


def seal(rows, weight, out):
    if sha(HERE / "protocol.json") != PROTOCOL_SHA:
        raise ValueError("Protocol changed")
    out.mkdir(parents=True, exist_ok=False)
    with (out / "matrices.npy").open("xb") as f:
        for row in rows:
            if "label" in row:
                raise ValueError("Target score present in prediction inputs")
            if "gap" in row:
                continue
            pair = paired_matrices(row["h2h"], row["total"], weight)
            row["offset"] = f.tell()
            np.save(f, np.stack(pair), allow_pickle=False)
    dump(out / "roster.json", rows)
    lock = dict(protocol_sha256=PROTOCOL_SHA, matrices_sha256=sha(out / "matrices.npy"),
                roster_sha256=sha(out / "roster.json"), registered=len(rows),
                paired=sum("offset" in r for r in rows), lambda_fitted=weight)
    dump(out / "lock.json", lock)
    return lock


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if sha(args.archive) != PROTOCOL["source_assets_sha256"] or sha(HERE / "protocol.json") != PROTOCOL_SHA:
        raise ValueError("Unexpected source edition")
    train = list(identities(read_csvs(args.archive, PROTOCOL["fit_seasons"], True)))
    valid = [r for r in train if r["label"] is not None and "gap" not in r]
    weight = fit_weight((r["h2h"], wdl(r["label"])) for r in valid)
    dev = list(identities(read_csvs(args.archive, PROTOCOL["development_seasons"], False)))
    latest = max(r["date"] for r in train)
    if latest >= min(r["date"] for r in dev) or {r["match_id"] for r in train} & {r["match_id"] for r in dev}:
        raise ValueError("Training and evaluation overlap")
    seal(dev, weight, args.out)
    dump(args.out / "fit.json",dict(weight=weight, raw=len(train), included=len(valid),
         by_competition=tier_counts(valid), min_date=min(r["date"] for r in train),
         max_date=latest, development_first=min(r["date"] for r in dev),
         development_last=max(r["date"] for r in dev), source_sha256=sha(args.archive)))


if __name__ == "__main__":
    main()
