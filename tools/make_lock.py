"""Merge several `pip install --dry-run --report` JSON files into one hashed requirements.lock.

Stdlib only. Input: report files (one per target platform/python). pip itself did the dependency
resolution and recorded the sha256 of the exact file it would install. Output: for every package
the union of hashes across the targets. Refuses to continue if versions differ between targets.

usage: make_lock.py OUT_LOCK REQUIREMENTS_IN REPORT.json [REPORT.json ...]
"""
import json
import sys
from collections import defaultdict


def norm(n: str) -> str:
    return n.lower().replace("_", "-").replace(".", "-")


def main(out, req_in, *reports):
    versions, hashes, files = {}, defaultdict(set), defaultdict(list)
    for rp in reports:
        for item in json.load(open(rp))["install"]:
            name, ver = item["metadata"]["name"], item["metadata"]["version"]
            key = norm(name)
            if versions.setdefault(key, (name, ver)) [1] != ver:
                sys.exit(f"version mismatch for {name}: {versions[key][1]} vs {ver} ({rp})")
            h = item["download_info"]["archive_info"]["hashes"]["sha256"]
            hashes[key].add(h)
            files[key].append(item["download_info"]["url"].rsplit("/", 1)[1])
    direct = {norm(l.split("==")[0]) for l in open(req_in) if "==" in l and not l.startswith("#")}
    lines = [
        "# requirements.lock - GENERATED, do not edit by hand. See SUPPLY_CHAIN.md for regeneration.",
        "# Install: pip install --no-cache-dir --require-hashes --only-binary=:all: --no-deps -r requirements.lock",
        f"# {len(versions)} packages, {sum(len(v) for v in hashes.values())} hashes. Direct deps are marked.",
        "# Hashes cover the wheels pip selected for the targets listed in the reports given to make_lock.py.",
        "",
    ]
    for key in sorted(versions):
        name, ver = versions[key]
        if key in direct:  # comment on its OWN line: a trailing comment after "\\" breaks line continuation
            lines.append("# direct dependency")
        lines.append(f"{key}=={ver} \\")
        hs = sorted(hashes[key])
        for i, h in enumerate(hs):
            lines.append(f"    --hash=sha256:{h}" + (" \\" if i < len(hs) - 1 else ""))
    open(out, "w").write("\n".join(lines) + "\n")
    print(f"{len(versions)} packages, {sum(len(v) for v in hashes.values())} hashes -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
