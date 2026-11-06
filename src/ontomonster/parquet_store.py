# SPDX-License-Identifier: BSD-3-Clause
"""Store the versions of workspaces in Parquet files.

The file save/history.parquet holds the version history. It has the
content and the metadata as columns. Each row belongs to one workspace
and one version. An export adds a version with a full snapshot of the
terms of the workspace. A save does not add a version. A save writes the
working copy to save/working.parquet.

The columns are:
- workspace: the slug of the ontology or module of the row.
- version: the version string, for example "1" or "2". The store also
  reads the earlier form "major.minor", for example "0.1".
- vnum: a sort number, major * 1000 + minor. It finds the latest
  version.
- parent: the previous version string. It is empty for the first
  version.
- bump: "minor", "major" or "initial".
- ts: the ISO timestamp of the save.
- cited: a JSON array of the cited mid-level ontologies of each version
  of a workspace.
- term_id, term_name, group_id: term fields, repeated in each row.
- pred, val, dtype, pfx, relpfx: the predicate row that the user can
  edit.

To rebuild a version, filter the rows for the workspace and the version.
Then group the rows into the term-centric project model. The files are
small. A save reads the table, adds the new rows and rewrites the file.
There is one writer. The last write wins.
"""

from __future__ import annotations

import json
import datetime as _dt
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

HISTORY = "history.parquet"

COLUMNS = [
    "workspace",
    "version",
    "vnum",
    "parent",
    "bump",
    "ts",
    "cited",
    "groups",
    "term_id",
    "term_name",
    "group_id",
    "pred",
    "val",
    "dtype",
    "pfx",
    "relpfx",
]

_SCHEMA = pa.schema(
    [
        ("workspace", pa.string()),
        ("version", pa.string()),
        ("vnum", pa.int32()),
        ("parent", pa.string()),
        ("bump", pa.string()),
        ("ts", pa.string()),
        ("cited", pa.string()),
        ("groups", pa.string()),
        ("term_id", pa.string()),
        ("term_name", pa.string()),
        ("group_id", pa.string()),
        ("pred", pa.string()),
        ("val", pa.string()),
        ("dtype", pa.string()),
        ("pfx", pa.string()),
        ("relpfx", pa.string()),
    ]
)


# Version arithmetic.
def parse_version(v):
    """Read a version string.

    Accept an integer string and the earlier form "x.y". The string "3"
    gives (3, 0). The string "x.y" gives (x, y). An invalid value gives
    (0, 0).
    """
    try:
        s = str(v)
        if "." in s:
            a, b = s.split(".", 1)
            return int(a), int(b)
        return int(s), 0
    except Exception:  # noqa: BLE001
        return 0, 0


def vnum_of(v: str) -> int:
    a, b = parse_version(v)
    return a * 1000 + b


def next_version(current: str | None, bump: str = "export") -> str:
    """Return the next version string.

    Only an export creates a version. The versions are simple integers:
    none gives "1", "1" gives "2" and "2" gives "3". A save never
    changes the version.
    """
    if not current:
        return "1"
    a, _ = parse_version(current)
    return str(a + 1)


# Input and output helpers.
def _path(save_dir) -> Path:
    return Path(save_dir) / HISTORY


def _conform(t: pa.Table) -> pa.Table:
    """Make a table that is read from disk match the current schema.

    This lets the code load files that an older version wrote, for
    example files from before the groups column existed. The function
    adds each missing column with a default value of the correct type.
    It removes extra columns. It sets the column order and the types to
    those of _SCHEMA.
    """
    n = t.num_rows
    present = {f.name for f in t.schema}
    arrays = []
    for field in _SCHEMA:
        if field.name in present:
            col = t.column(field.name)
            if col.type != field.type:
                col = col.cast(field.type)
            arrays.append(col)
        elif field.type == pa.int32():
            arrays.append(pa.array([0] * n, type=field.type))
        else:
            arrays.append(pa.array([""] * n, type=field.type))
    return pa.Table.from_arrays(arrays, schema=_SCHEMA)


def _read_table(save_dir) -> pa.Table | None:
    p = _path(save_dir)
    if not p.is_file():
        return None
    return _conform(pq.read_table(p))


def latest_version(save_dir, workspace: str) -> str | None:
    t = _read_table(save_dir)
    if t is None:
        return None
    best, best_n = None, -1
    ws = t.column("workspace").to_pylist()
    ver = t.column("version").to_pylist()
    vn = t.column("vnum").to_pylist()
    for w, v, n in zip(ws, ver, vn):
        if w == workspace and n > best_n:
            best, best_n = v, n
    return best


def list_workspaces(save_dir) -> list[str]:
    t = _read_table(save_dir)
    if t is None:
        return []
    return sorted(set(t.column("workspace").to_pylist()))


def delete_workspace(save_dir, workspace: str) -> dict:
    """Remove a workspace from the Parquet store for good.

    The function removes every version row from history.parquet and the
    working copy row from working.parquet. It returns the counts. If a
    table is empty after the change, the function deletes its file. The
    store then stays clean.
    """
    save_dir = Path(save_dir)
    removed_versions = 0
    # history.parquet: keep each row where the workspace is not the
    # target.
    hp = _path(save_dir)
    if hp.is_file():
        t = _conform(pq.read_table(hp))
        ws = t.column("workspace").to_pylist()
        keep = [i for i, w in enumerate(ws) if w != workspace]
        removed_versions = len(
            set(
                v
                for w, v in zip(ws, t.column("version").to_pylist())
                if w == workspace
            )
        )
        if len(keep) == 0:
            hp.unlink()
        elif len(keep) != len(ws):
            pq.write_table(t.take(keep), hp.as_posix())
    # working.parquet: remove the one row of the workspace.
    wp = _working_path(save_dir)
    had_working = False
    if wp.is_file():
        d = pq.read_table(wp.as_posix()).to_pydict()
        keys = d["workspace"]
        keep = [i for i, k in enumerate(keys) if k != workspace]
        had_working = len(keep) != len(keys)
        if len(keep) == 0:
            wp.unlink()
        elif had_working:
            cols = {c: [d[c][i] for i in keep] for c in d}
            pq.write_table(pa.table(cols), wp.as_posix())
    return {
        "workspace": workspace,
        "removed_versions": removed_versions,
        "removed_working": had_working,
    }


def rename_workspace(save_dir, old: str, new: str) -> dict:
    """Change the key of a workspace from old to new.

    The function changes the key in history.parquet (every version row)
    and in working.parquet (the one row). All other columns and their
    types stay the same. The caller must check for collisions. The
    function returns the counts.
    """
    save_dir = Path(save_dir)
    changed_versions = 0
    changed_working = False
    # history.parquet: replace the values of the "workspace" column.
    # Keep the other columns the same.
    hp = _path(save_dir)
    if hp.is_file():
        t = _conform(pq.read_table(hp))
        ws = t.column("workspace").to_pylist()
        if old in ws:
            changed_versions = sum(1 for w in ws if w == old)
            idx = t.column_names.index("workspace")
            newarr = pa.array(
                [new if w == old else w for w in ws], type=pa.string()
            )
            t = t.set_column(idx, "workspace", newarr)
            pq.write_table(t, hp.as_posix())
    # working.parquet: change the key in the same way. The dictionary
    # round trip uses the same pattern as delete_workspace.
    wp = _working_path(save_dir)
    if wp.is_file():
        d = pq.read_table(wp.as_posix()).to_pydict()
        keys = d.get("workspace", [])
        if old in keys:
            d["workspace"] = [new if k == old else k for k in keys]
            changed_working = True
            pq.write_table(pa.table(d), wp.as_posix())
    return {
        "from": old,
        "to": new,
        "versions": changed_versions,
        "working": changed_working,
    }


def list_versions(save_dir, workspace: str) -> list[dict]:
    t = _read_table(save_dir)
    if t is None:
        return []
    seen, out = {}, []
    cols = {
        c: t.column(c).to_pylist()
        for c in ("workspace", "version", "vnum", "bump", "ts")
    }
    for i in range(t.num_rows):
        if cols["workspace"][i] != workspace:
            continue
        v = cols["version"][i]
        if v in seen:
            continue
        seen[v] = 1
        out.append(
            {
                "version": v,
                "vnum": cols["vnum"][i],
                "bump": cols["bump"][i],
                "ts": cols["ts"][i],
            }
        )
    out.sort(key=lambda r: r["vnum"])
    return out


# Conversion between terms and rows.
def _terms_to_rows(terms, meta) -> list[dict]:
    rows = []
    for t in terms or []:
        tid = str(t.get("id") or t.get("name") or "")
        tname = t.get("name", "")
        gid = t.get("groupId") or t.get("group_id") or ""
        children = t.get("children", [])
        if not children:
            children = [
                {"pred": "", "val": "", "dtype": "", "pfx": "", "relPfx": ""}
            ]
        for c in children:
            r = dict(meta)
            r.update(
                {
                    "term_id": tid,
                    "term_name": tname,
                    "group_id": gid,
                    "pred": c.get("pred", "") or "",
                    "val": c.get("val", "") or "",
                    "dtype": c.get("dtype", "") or "",
                    "pfx": c.get("pfx", "") or "",
                    "relpfx": c.get("relPfx", "") or "",
                }
            )
            rows.append(r)
    return rows


def _rows_to_terms(rows) -> list[dict]:
    by_term, order = {}, []
    for r in rows:
        tid = r["term_id"]
        if not tid and not r["term_name"]:
            # This row is a placeholder with metadata only. The
            # workspace had no terms.
            continue
        if tid not in by_term:
            by_term[tid] = {
                "id": tid,
                "name": r["term_name"],
                "groupId": r["group_id"] or None,
                "children": [],
            }
            order.append(tid)
        if r["pred"]:
            by_term[tid]["children"].append(
                {
                    "pred": r["pred"],
                    "val": r["val"],
                    "dtype": r["dtype"],
                    "pfx": r["pfx"] or None,
                    "relPfx": r["relpfx"] or None,
                }
            )
    return [by_term[t] for t in order]


# Public API.
def save_version(
    save_dir,
    workspace: str,
    terms,
    bump: str,
    cited: list | None = None,
    groups: list | None = None,
) -> dict:
    """Append a new version snapshot of the workspace. Return the
    version information.
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    cur = latest_version(save_dir, workspace)
    bump = "initial" if cur is None else "export"
    ver = next_version(cur, bump)
    meta = {
        "workspace": workspace,
        "version": ver,
        "vnum": vnum_of(ver),
        "parent": cur or "",
        "bump": bump,
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "cited": json.dumps(cited or []),
        "groups": json.dumps(groups or []),
    }
    new_rows = _terms_to_rows(terms, meta)
    new_tbl = (
        pa.Table.from_pylist(new_rows, schema=_SCHEMA)
        if new_rows
        else pa.Table.from_pylist(
            [
                {
                    **meta,
                    **{
                        k: ""
                        for k in (
                            "term_id",
                            "term_name",
                            "group_id",
                            "pred",
                            "val",
                            "dtype",
                            "pfx",
                            "relpfx",
                        )
                    },
                }
            ],
            schema=_SCHEMA,
        )
    )
    existing = _read_table(save_dir)
    table = (
        pa.concat_tables([existing, new_tbl])
        if existing is not None
        else new_tbl
    )
    pq.write_table(table, _path(save_dir))
    n_terms = len({r["term_id"] for r in new_rows}) if new_rows else 0
    print(
        f"[parquet] saved {workspace} v{ver} ({bump}, {n_terms} terms, "
        f"{len(groups or [])} groups, parent={cur or '-'})"
    )
    return {
        "workspace": workspace,
        "version": ver,
        "bump": bump,
        "parent": cur,
        "terms": n_terms,
        "groups": len(groups or []),
    }


def load_version(save_dir, workspace: str, version: str | None = None) -> dict:
    """Rebuild the term-centric project of a workspace for one version.

    Use the latest version if version is None.
    """
    t = _read_table(save_dir)
    if t is None:
        return {
            "workspace": workspace,
            "version": None,
            "terms": [],
            "cited": [],
            "groups": [],
        }
    if version is None:
        version = latest_version(save_dir, workspace)
    cols = {c: t.column(c).to_pylist() for c in COLUMNS}
    rows, cited, groups = [], [], []
    for i in range(t.num_rows):
        if cols["workspace"][i] != workspace or cols["version"][i] != version:
            continue
        rows.append({c: cols[c][i] for c in COLUMNS})
        if not cited and cols["cited"][i]:
            try:
                cited = json.loads(cols["cited"][i])
            except Exception:  # noqa: BLE001
                cited = []
        if not groups and cols["groups"][i]:
            try:
                groups = json.loads(cols["groups"][i])
            except Exception:  # noqa: BLE001
                groups = []
    return {
        "workspace": workspace,
        "version": version,
        "terms": _rows_to_terms(rows),
        "cited": cited,
        "groups": groups,
    }


# Working snapshots. A working snapshot is the draft that the user is
# editing now. A save writes the snapshot here. A save does not create a
# version. An export creates a version: it moves the working snapshot
# into history.parquet.
WORKING = "working.parquet"


def _working_path(save_dir) -> Path:
    return Path(save_dir) / WORKING


def save_working(
    save_dir, workspace: str, terms, groups=None, cited=None
) -> dict:
    p = _working_path(save_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if p.exists():
        d = pq.read_table(p.as_posix()).to_pydict()
        for i in range(len(d["workspace"])):
            data[d["workspace"][i]] = {
                k: d[k][i] for k in ("terms", "groups", "cited", "ts")
            }
    ts = _dt.datetime.now().isoformat(timespec="seconds")
    data[workspace] = {
        "terms": json.dumps(terms or []),
        "groups": json.dumps(groups or []),
        "cited": json.dumps(cited or []),
        "ts": ts,
    }
    keys = list(data.keys())
    pq.write_table(
        pa.table(
            {
                "workspace": keys,
                "terms": [data[k]["terms"] for k in keys],
                "groups": [data[k]["groups"] for k in keys],
                "cited": [data[k]["cited"] for k in keys],
                "ts": [data[k]["ts"] for k in keys],
            }
        ),
        p.as_posix(),
    )
    return {"workspace": workspace, "saved": True, "ts": ts}


def load_working(save_dir, workspace: str) -> dict | None:
    p = _working_path(save_dir)
    if not p.exists():
        return None
    d = pq.read_table(p.as_posix()).to_pydict()
    for i in range(len(d["workspace"])):
        if d["workspace"][i] == workspace:
            return {
                "workspace": workspace,
                "terms": json.loads(d["terms"][i] or "[]"),
                "groups": json.loads(d["groups"][i] or "[]"),
                "cited": json.loads(d["cited"][i] or "[]"),
                "ts": d["ts"][i],
            }
    return None


def list_working(save_dir) -> list[str]:
    p = _working_path(save_dir)
    if not p.exists():
        return []
    return list(pq.read_table(p.as_posix()).to_pydict()["workspace"])
