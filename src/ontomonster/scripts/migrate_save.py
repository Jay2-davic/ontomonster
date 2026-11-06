#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Split a pooled save/ folder into one save/ folder for each workspace.

The earlier layout kept one pooled store in the save/ folder. The store
has the files history.parquet and working.parquet. All workspaces are
mixed in these files.

This script writes the rows of each workspace to the save/ folder of the
matching workspace folder: <internal>/<org>/<workspace-folder>/save/. It
groups the rows by the workspace column. A folder matches a workspace
when slugify(folder) is equal to the workspace slug. The script searches
all org folders. It skips each workspace that has no matching folder and
reports it.

The script does not delete files. It only writes new save/ files.

Usage:
    python scripts/migrate_save.py --pooled OLD --internal DIR

OLD is the pooled save/ folder. DIR is the internal-ontology folder. Add
the option --dry-run to show the result without a write to disk.

The current layout uses one save/ folder for each org. The app also
reads the per-workspace folders that this script writes.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

import pyarrow.parquet as pq

HISTORY = "history.parquet"
WORKING = "working.parquet"
RESERVED = {"save", "refcache", "ontologies", "ttl", "_file_template", ".git"}


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def _folder_for(internal: str, slug: str):
    """Find the folder <internal>/<org>/<folder> with a matching slug.
    Return the org and the folder path.
    """
    for org in sorted(os.listdir(internal)):
        od = os.path.join(internal, org)
        if not os.path.isdir(od) or org in RESERVED or org.startswith("."):
            continue
        for d in sorted(os.listdir(od)):
            dp = os.path.join(od, d)
            if (
                os.path.isdir(dp)
                and d not in RESERVED
                and not d.endswith("_consolidate")
                and slugify(d) == slug
            ):
                return org, dp
    return None, None


def _split(path, out_name, internal, dry):
    if not os.path.isfile(path):
        print(f"  (no {os.path.basename(path)} — skipping)")
        return {}, []
    t = pq.read_table(path)
    if "workspace" not in t.column_names:
        print(f"  WARN: {path} has no 'workspace' column; cannot split")
        return {}, []
    col = t.column("workspace").to_pylist()
    slugs = sorted(set(slugify(w) for w in col if w))
    written, unmatched = {}, []
    for slug in slugs:
        mask = [slugify(w) == slug for w in col]
        sub = t.filter(mask)
        org, folder = _folder_for(internal, slug)
        if not folder:
            unmatched.append(slug)
            continue
        dest_dir = os.path.join(folder, "save")
        dest = os.path.join(dest_dir, out_name)
        written[slug] = (org, dest, sub.num_rows)
        if not dry:
            os.makedirs(dest_dir, exist_ok=True)
            pq.write_table(sub, dest)
    return written, unmatched


def main(argv):
    ap = argparse.ArgumentParser(
        description="Split pooled save/ into per-workspace save/ folders."
    )
    ap.add_argument(
        "--pooled",
        required=True,
        help="old pooled save/ folder (has history.parquet / working.parquet)",
    )
    ap.add_argument(
        "--internal",
        required=True,
        help="internal-ontology location (contains the org folders)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.pooled):
        raise SystemExit(f"pooled save folder not found: {args.pooled}")
    if not os.path.isdir(args.internal):
        raise SystemExit(f"internal location not found: {args.internal}")

    mode = "DRY RUN — " if args.dry_run else ""
    print(f"{mode}splitting pooled save into per-workspace folders\n")
    all_unmatched = set()
    for src_name, out_name, label in (
        (WORKING, WORKING, "working drafts"),
        (HISTORY, HISTORY, "version history"),
    ):
        print(f"{label} ({src_name}):")
        written, unmatched = _split(
            os.path.join(args.pooled, src_name),
            out_name,
            args.internal,
            args.dry_run,
        )
        for slug, (org, dest, rows) in written.items():
            print(f"  {slug:<28} -> {org}/.../save/{out_name}  ({rows} rows)")
        for u in unmatched:
            all_unmatched.add(u)
        print()
    if all_unmatched:
        print(
            "UNMATCHED workspaces (no folder found — create <org>/<folder>/ "
            "for these, then re-run):"
        )
        for u in sorted(all_unmatched):
            print(f"  {u}")
    print(
        "\nDone."
        + (
            ""
            if args.dry_run
            else "  Review the new save/ folders, then work as usual."
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
