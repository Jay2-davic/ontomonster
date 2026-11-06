# SPDX-License-Identifier: BSD-3-Clause
"""Read a spreadsheet or CSV file into a plain 2D grid of cells.

The inspector UI shows the grid. The user labels which cells are
variables (headers) and which cells are values. This is important for
untidy Excel files, where a plain CSV parse guesses wrong. The module
only reads the raw layout of the cells. It makes no semantic guesses.
"""

from __future__ import annotations

import csv
import io

_MAX_ROWS = 300
_MAX_COLS = 80


def _flat(v) -> str:
    """Convert a scalar to a string. Convert a nested object or array to
    compact JSON, so that it fits in one cell.
    """
    if v is None:
        return ""
    try:
        import math

        if isinstance(v, float) and math.isnan(v):
            return ""
    except Exception:
        pass
    if isinstance(v, (dict, list, tuple)):
        import json as _j

        try:
            return _j.dumps(v, ensure_ascii=False)[:300]
        except Exception:
            return str(v)[:300]
    return str(v)


def _json_to_grid(data, max_rows: int, max_cols: int) -> list:
    """Convert parsed JSON to a 2D grid for the inspector.

    - A list of objects: one column for each key (the union of the keys)
      and one row for each object.
    - A dictionary of lists: one column for each key.
    - A dictionary of scalars: a table of keys and values.
    - A list of scalars: one column named "value".
    """
    rows: list = []
    if isinstance(data, list):
        if data and all(isinstance(x, dict) for x in data):
            cols: list = []
            for x in data:
                for k in x.keys():
                    if k not in cols:
                        cols.append(k)
            cols = cols[:max_cols]
            rows.append([str(c) for c in cols])
            for x in data[: max_rows - 1]:
                rows.append([_flat(x.get(c, "")) for c in cols])
        else:
            rows.append(["value"])
            for x in data[: max_rows - 1]:
                rows.append([_flat(x)])
    elif isinstance(data, dict):
        if data and all(isinstance(v, list) for v in data.values()):
            cols = list(data.keys())[:max_cols]
            rows.append([str(c) for c in cols])
            n = max((len(data[c]) for c in cols), default=0)
            for i in range(min(n, max_rows - 1)):
                rows.append(
                    [
                        _flat(data[c][i]) if i < len(data[c]) else ""
                        for c in cols
                    ]
                )
        else:
            rows.append(["key", "value"])
            for k, v in list(data.items())[: max_rows - 1]:
                rows.append([str(k), _flat(v)])
    else:
        rows.append(["value"])
        rows.append([_flat(data)])
    return rows


def _sniff_delim(text: str) -> str:
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except Exception:
        # If detection fails, use the common delimiter that occurs most
        # often in the first line.
        first = sample.splitlines()[0] if sample.splitlines() else ""
        return max(",\t;|", key=lambda d: first.count(d)) if first else ","


def read_grid(
    filename: str,
    raw_text: str | None = None,
    raw_bytes: bytes | None = None,
    max_rows: int = _MAX_ROWS,
    max_cols: int = _MAX_COLS,
) -> dict:
    name = (filename or "").lower()
    sheets: list[dict] = []
    truncated = False

    if name.endswith((".xlsx", ".xlsm", ".xltx")):
        import openpyxl

        wb = openpyxl.load_workbook(
            io.BytesIO(raw_bytes or b""), data_only=True
        )
        for ws in wb.worksheets:
            merges = [
                [m.min_row - 1, m.min_col - 1, m.max_row - 1, m.max_col - 1]
                for m in ws.merged_cells.ranges
            ]
            rows: list[list[str]] = []
            for r, row in enumerate(ws.iter_rows(values_only=True)):
                if r >= max_rows:
                    truncated = True
                    break
                if len(row) > max_cols:
                    truncated = True
                rows.append(
                    ["" if v is None else str(v) for v in row[:max_cols]]
                )
            sheets.append(
                {
                    "name": ws.title,
                    "rows": rows,
                    "merges": merges,
                    "nrows": ws.max_row or len(rows),
                    "ncols": min(ws.max_column or 0, max_cols),
                }
            )
    elif name.endswith((".parquet", ".pq")):
        buf = io.BytesIO(raw_bytes or b"")
        cols: list = []
        coldata: dict = {}
        nrows = 0
        try:
            # This import is light. It does not need pandas.
            import pyarrow.parquet as _pq

            tbl = _pq.read_table(buf)
            cols = [str(c) for c in tbl.column_names[:max_cols]]
            d = tbl.to_pydict()
            coldata = {c: d.get(c, []) for c in cols}
            nrows = tbl.num_rows
        except ImportError:
            try:
                import pandas as pd  # Use pandas if it is installed.

                df = pd.read_parquet(buf)
                cols = [str(c) for c in list(df.columns)[:max_cols]]
                coldata = {c: list(df[c]) for c in cols}
                nrows = int(df.shape[0])
            except ImportError:
                raise RuntimeError(
                    "reading Parquet needs the 'pyarrow' package "
                    "(pip install pyarrow) on the machine running the server"
                )
        rows = [cols]
        for i in range(min(nrows, max_rows - 1)):
            rows.append(
                [
                    _flat(coldata[c][i]) if i < len(coldata[c]) else ""
                    for c in cols
                ]
            )
        if len(cols) >= max_cols:
            truncated = True
        sheets.append(
            {
                "name": "Data",
                "rows": rows,
                "merges": [],
                "nrows": nrows + 1,
                "ncols": len(cols),
            }
        )
    elif name.endswith(".json"):
        import json as _json

        text = (
            raw_text
            if raw_text is not None
            else (raw_bytes or b"").decode("utf-8", "replace")
        )
        if text[:1] == "\ufeff":
            text = text[1:]
        rows = _json_to_grid(_json.loads(text or "null"), max_rows, max_cols)
        ncols = max((len(r) for r in rows), default=0)
        sheets.append(
            {
                "name": "Data",
                "rows": rows,
                "merges": [],
                "nrows": len(rows),
                "ncols": ncols,
            }
        )
    else:
        text = (
            raw_text
            if raw_text is not None
            else ((raw_bytes or b"").decode("utf-8", "replace"))
        )
        if text[:1] == "\ufeff":
            text = text[1:]
        delim = "\t" if name.endswith((".tsv", ".tab")) else _sniff_delim(text)
        rows = []
        for r, row in enumerate(
            csv.reader(io.StringIO(text), delimiter=delim)
        ):
            if r >= max_rows:
                truncated = True
                break
            if len(row) > max_cols:
                truncated = True
            rows.append([str(c) for c in row[:max_cols]])
        ncols = max((len(r) for r in rows), default=0)
        sheets.append(
            {
                "name": "Sheet1",
                "rows": rows,
                "merges": [],
                "nrows": len(rows),
                "ncols": ncols,
            }
        )

    return {"filename": filename, "sheets": sheets, "truncated": truncated}
