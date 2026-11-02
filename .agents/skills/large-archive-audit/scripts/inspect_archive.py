#!/usr/bin/env python3
"""Read-only safety and inventory inspection for large ZIP/TAR archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_member(name: str) -> tuple[str, list[str]]:
    clean = name.replace("\\", "/")
    pure = PurePosixPath(clean)
    issues: list[str] = []
    if pure.is_absolute() or clean.startswith("/"):
        issues.append("absolute_path")
    if any(part == ".." for part in pure.parts):
        issues.append("path_traversal")
    if "\x00" in clean:
        issues.append("nul_byte")
    normalized = str(PurePosixPath(*[p for p in pure.parts if p not in ("", ".")]))
    return normalized, issues


def inspect_zip(path: Path) -> tuple[list[dict], list[dict]]:
    members: list[dict] = []
    issues: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            normalized, member_issues = normalize_member(item.filename)
            mode = (item.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                member_issues.append("symlink")
            if item.flag_bits & 0x1:
                member_issues.append("encrypted")
            row = {
                "name": item.filename,
                "normalized": normalized,
                "compressed_bytes": item.compress_size,
                "uncompressed_bytes": item.file_size,
                "is_directory": item.is_dir(),
                "issues": sorted(set(member_issues)),
            }
            members.append(row)
            for issue in row["issues"]:
                issues.append({"member": item.filename, "issue": issue})
    return members, issues


def inspect_tar(path: Path) -> tuple[list[dict], list[dict]]:
    members: list[dict] = []
    issues: list[dict] = []
    with tarfile.open(path, mode="r:*") as archive:
        for item in archive:
            normalized, member_issues = normalize_member(item.name)
            if item.issym() or item.islnk():
                member_issues.append("link")
            if item.isdev() or item.isfifo():
                member_issues.append("special_file")
            row = {
                "name": item.name,
                "normalized": normalized,
                "compressed_bytes": None,
                "uncompressed_bytes": item.size,
                "is_directory": item.isdir(),
                "issues": sorted(set(member_issues)),
            }
            members.append(row)
            for issue in row["issues"]:
                issues.append({"member": item.name, "issue": issue})
    return members, issues


def build_report(path: Path, max_total: int, max_member: int, max_ratio: float) -> dict:
    archive_size = path.stat().st_size
    if zipfile.is_zipfile(path):
        archive_type = "zip"
        members, issues = inspect_zip(path)
    elif tarfile.is_tarfile(path):
        archive_type = "tar"
        members, issues = inspect_tar(path)
    else:
        raise ValueError("unsupported or invalid archive; expected ZIP or TAR family")

    seen: dict[str, str] = {}
    for member in members:
        key = member["normalized"].casefold()
        if key in seen:
            issues.append(
                {
                    "member": member["name"],
                    "issue": "duplicate_normalized_path",
                    "first": seen[key],
                }
            )
        else:
            seen[key] = member["name"]

    total_uncompressed = sum(m["uncompressed_bytes"] for m in members)
    largest = max((m["uncompressed_bytes"] for m in members), default=0)
    ratio = total_uncompressed / max(archive_size, 1)
    warnings: list[str] = []
    if total_uncompressed > max_total:
        warnings.append("total_uncompressed_limit_exceeded")
    if largest > max_member:
        warnings.append("single_member_limit_exceeded")
    if ratio > max_ratio:
        warnings.append("expansion_ratio_limit_exceeded")

    return {
        "archive": str(path.resolve()),
        "archive_type": archive_type,
        "archive_bytes": archive_size,
        "sha256": sha256_file(path),
        "member_count": len(members),
        "total_uncompressed_bytes": total_uncompressed,
        "largest_member_bytes": largest,
        "expansion_ratio": round(ratio, 3),
        "unsafe": bool(issues or warnings),
        "issues": issues,
        "warnings": warnings,
        "members": members,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--json", type=Path, dest="json_path")
    parser.add_argument("--max-total-gib", type=float, default=8.0)
    parser.add_argument("--max-member-gib", type=float, default=2.0)
    parser.add_argument("--max-ratio", type=float, default=200.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = args.archive.resolve()
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    try:
        report = build_report(
            path,
            max_total=int(args.max_total_gib * 1024**3),
            max_member=int(args.max_member_gib * 1024**3),
            max_ratio=args.max_ratio,
        )
    except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_path:
        args.json_path.write_text(output + "\n", encoding="utf-8")
    summary = {
        key: report[key]
        for key in (
            "archive",
            "archive_type",
            "archive_bytes",
            "sha256",
            "member_count",
            "total_uncompressed_bytes",
            "largest_member_bytes",
            "expansion_ratio",
            "unsafe",
            "warnings",
        )
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if report["unsafe"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
