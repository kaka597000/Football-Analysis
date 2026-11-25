"""Check the documented module and route inventory using local files."""
import argparse
import hashlib
import json
from pathlib import Path

from runtime.competition_scope import CONFIG, inventory


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "configs/module-portfolio.json"
ROUTE_LISTS = ("retained_active_routes", "historical_only_routes", "missing_competitions")


def check(manifest, registry_path=None):
    registered = manifest["routing"]["recorded_registry"]
    roots = [item["path"] for item in manifest["modules"]]
    if len(set(roots)) != len(roots):
        raise ValueError("duplicate module group")

    errors = []
    for item in manifest["modules"] + manifest["candidates"]:
        if item["status"] not in manifest["states"]:
            errors.append(f"unrecognized status: {item['status']}")
    for root in roots:
        if not (ROOT / root).exists():
            errors.append(f"missing module group: {root}")

    sources = sorted(
        path.relative_to(ROOT).as_posix() for path in (ROOT / "runtime").rglob("*.py")
        if not path.name.startswith("test_")
    )
    for source in sources:
        if not any(source == root or source.startswith(root + "/") for root in roots):
            errors.append(f"unclassified source: {source}")

    if ROOT / manifest["routing"]["scope_path"] != CONFIG:
        errors.append("inventory must refer to the formal scope")
    route_keys = registered["retained_active_routes"] + registered["historical_only_routes"]
    if len(route_keys) != len(set(route_keys)):
        errors.append("duplicate recorded route")

    # Without an explicit registry, reconcile only the recorded keys with policy.
    registry = {"models": dict.fromkeys(route_keys)}
    if registry_path is not None:
        contents = registry_path.read_bytes()
        registry = json.loads(contents)
        if (hashlib.sha256(contents).hexdigest() != registered["sha256"]
                or any(registry.get(key) != registered[key] for key in ("version", "status"))):
            errors.append("registry identity differs from the recorded snapshot")

    scope = inventory(registry)
    if scope["scope_count"] != registered["scope_count"]:
        errors.append("formal scope count differs from the snapshot")
    for key in ROUTE_LISTS:
        if sorted(scope[key]) != sorted(registered[key]):
            errors.append(f"route inventory differs: {key}")

    return {
        "module_groups": len(roots),
        "runtime_sources": len(sources),
        "registry_verified": registry_path is not None and not errors,
        "scope_count": scope["scope_count"],
        "in_scope_routes": len(scope["retained_active_routes"]),
        "historical_routes": len(scope["historical_only_routes"]),
        "missing_competitions": scope["missing_competitions"],
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, help="Optional original registry for snapshot verification")
    args = parser.parse_args()
    report = check(json.loads(MANIFEST.read_text(encoding="utf-8")), args.registry)
    print(json.dumps(report, indent=2))
    return bool(report["errors"])


if __name__ == "__main__":
    raise SystemExit(main())
