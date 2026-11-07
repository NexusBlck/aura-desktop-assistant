# cdc_excel.py
# Turns CDC WONDER results into clean, research-ready Excel workbooks:
# one workbook per condition, one sheet per grouping, 1999-2020 (bridged-race
# dataset) + 2021 onward (single-race dataset) joined with no repeated years,
# numbers stored as real numbers, suppressed/unreliable values clearly marked.
# Also reads the files you export from the CDC WONDER website (location data).
import os
import re
import csv
import json
import time
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import cdc_wonder as W

COLUMNS = [  # (key, header, number format)
    ("deaths", "Deaths", "#,##0"),
    ("population", "Population", "#,##0"),
    ("crude", "Crude Rate (per 100,000)", "0.00"),
    ("crude_lo", "Crude Rate 95% CI Lower", "0.00"),
    ("crude_hi", "Crude Rate 95% CI Upper", "0.00"),
    ("crude_se", "Crude Rate SE", "0.00"),
    ("aar", "Age-Adjusted Rate (per 100,000)", "0.00"),
    ("aar_lo", "AAR 95% CI Lower", "0.00"),
    ("aar_hi", "AAR 95% CI Upper", "0.00"),
    ("aar_se", "AAR Standard Error", "0.00"),
]
FLAG_TEXT = {"suppressed": "Suppressed (1-9 deaths, hidden by CDC)", "unreliable": "Unreliable (fewer than 20 deaths)",
             "not applicable": "Not applicable", "missing": "Missing"}


# ------------------------------------------------------------------ merging
def merge_years(d77_rows, d157_rows):
    """1999-2020 from the 1999-2020 dataset, 2021+ from the 2018-2024 one -
    so 2018-2020 (in both) appears once."""
    rows = [r for r in d77_rows if r["year"] <= W.PRIMARY_UNTIL] + [r for r in d157_rows if r["year"] > W.PRIMARY_UNTIL]
    return sorted(rows, key=lambda r: (r["group"], r["year"]))


def _notes(row):
    flags = sorted({f.split(":", 1)[1] for f in row.get("flags", [])})
    return "; ".join(FLAG_TEXT.get(f, f) for f in flags)


# ------------------------------------------------------------------ website exports (location data)
_GROUP_HEADERS = {"Census Region": "census", "State": "state", "2013 Urbanization": "urban"}
_MEASURE_HEADERS = {
    "deaths": "deaths", "population": "population", "crude rate": "crude",
    "crude rate lower 95% confidence interval": "crude_lo", "crude rate upper 95% confidence interval": "crude_hi",
    "crude rate standard error": "crude_se", "age adjusted rate": "aar",
    "age adjusted rate lower 95% confidence interval": "aar_lo", "age adjusted rate upper 95% confidence interval": "aar_hi",
    "age adjusted rate standard error": "aar_se",
}


def parse_web_export(path):
    """Reads a results file exported from the CDC WONDER website. Returns
    {"db","grouping","codes","rows","file"} or None if it isn't a CDC WONDER
    Multiple Cause of Death export for Census Region / State / Urbanization."""
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    if "Multiple Cause of Death" not in text:
        return None
    lines = text.splitlines()
    header_idx = next((i for i, l in enumerate(lines) if re.search(r'"?Notes"?[\t,]', l)), None)
    if header_idx is None:
        return None
    delim = "\t" if lines[header_idx].count("\t") >= lines[header_idx].count(",") else ","
    end = next((i for i in range(header_idx + 1, len(lines)) if lines[i].strip().strip('"') == "---"), len(lines))
    table = list(csv.reader(lines[header_idx:end], delimiter=delim, quotechar='"'))
    footer = "\n".join(lines[end:])
    header = [h.strip() for h in table[0]]

    group_col = next((i for i, h in enumerate(header) if h in _GROUP_HEADERS), None)
    year_col = next((i for i, h in enumerate(header) if h == "Year"), None)
    if group_col is None or year_col is None:
        return None
    db = "D157" if re.search(r"2018\s*-\s*20\d\d", footer + text[:500]) else "D77"
    codes = sorted(set(re.findall(r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b", "\n".join(
        l for l in footer.splitlines() if re.search(r"cause of death", l, re.I)))))

    rows = []
    for rec in table[1:]:
        if len(rec) <= max(group_col, year_col) or (rec and rec[0].strip().lower() == "total"):
            continue  # totals / blank lines
        group, year = rec[group_col].strip(), re.sub(r"\D", "", rec[year_col])
        if not group or not year:
            continue
        row = {"group": group, "year": int(year), "dataset": db, "flags": []}
        for i, h in enumerate(header):
            key = _MEASURE_HEADERS.get(h.lower())
            if not key or i >= len(rec):
                continue
            raw = rec[i].strip()
            low = raw.lower()
            for flag in ("suppressed", "unreliable", "not applicable", "missing"):
                if flag in low:
                    row["flags"].append(f"{key}:{flag}")
            num = re.sub(r"\(.*?\)|,", "", raw).strip()
            try:
                row[key] = float(num)
            except ValueError:
                row[key] = raw if key == "deaths" and raw else None
        rows.append(row)
    return {"db": db, "grouping": _GROUP_HEADERS[header[group_col]], "codes": codes, "rows": rows, "file": path}


def find_web_exports(since, folder=None):
    """New CDC WONDER exports in Downloads since the job started."""
    folder = folder or os.path.join(os.path.expanduser("~"), "Downloads")
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return found
    for name in names:
        path = os.path.join(folder, name)
        if not name.lower().endswith((".txt", ".tsv", ".csv", ".xls")) or "cause of death" not in name.lower():
            continue
        try:
            if os.path.getmtime(path) < since - 5:
                continue
        except OSError:
            continue
        parsed = parse_web_export(path)
        if parsed:
            found.append(parsed)
    return found


def match_export(job, export):
    """Which condition an export belongs to (by its ICD codes)."""
    best, best_score = None, 0
    for cond in job.spec["conditions"]:
        overlap = len(set(cond["codes"]) & set(export["codes"]))
        if overlap > best_score:
            best, best_score = cond, overlap
    if best is None and len(job.spec["conditions"]) == 1:
        best = job.spec["conditions"][0]  # only one condition - it must be this
    return best


# ------------------------------------------------------------------ workbook
HEADER_FILL = PatternFill("solid", fgColor="0B3A4A")
HEADER_FONT = Font(bold=True, color="FFFFFF")
FLAG_FONT = Font(italic=True, color="7F7F7F")
THIN = Side(style="thin", color="D0D7DC")


def _write_sheet(wb, title, group_header, rows, note=None):
    ws = wb.create_sheet(title[:31])
    used = [c for c in COLUMNS if any(r.get(c[0]) is not None for r in rows)]
    headers = ([group_header] if group_header else []) + ["Year"] + [h for _, h, _ in used] + ["Notes", "Source dataset"]
    start = 1
    if note:
        ws.cell(row=1, column=1, value=note).font = Font(italic=True, color="9C5700")
        start = 3
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=start, column=col, value=h)
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[start].height = 32
    for i, r in enumerate(rows, start + 1):
        values = ([r["group"]] if group_header else []) + [r["year"]] + [r.get(k) for k, _, _ in used] + \
                 [_notes(r), W.DATASETS[r["dataset"]]["short"]]
        for col, v in enumerate(values, 1):
            c = ws.cell(row=i, column=col, value=v)
            c.border = Border(bottom=THIN)
        for j, (_, _, fmt) in enumerate(used):
            ws.cell(row=i, column=(2 if group_header else 1) + 1 + j).number_format = fmt
        if r.get("flags"):
            for col in range(1, len(values) + 1):
                ws.cell(row=i, column=col).font = FLAG_FONT
    widths = [max(len(str(h)), 10) for h in headers]
    for r in rows[:200]:
        if group_header:
            widths[0] = max(widths[0], min(len(str(r["group"])), 40))
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = min(w + 2, 42)
    ws.column_dimensions[get_column_letter(len(headers) - 1)].width = 34
    ws.freeze_panes = ws.cell(row=start + 1, column=(3 if group_header else 2))
    ws.auto_filter.ref = f"A{start}:{get_column_letter(len(headers))}{start + max(len(rows), 1)}"
    return ws


def build_workbook(job, cond, web_imports):
    """Writes '<condition> - CDC WONDER.xlsx' and returns its path."""
    wb = Workbook()
    about = wb.active
    about.title = "About"
    spec = job.spec
    ages = spec.get("ages") or []
    age_labels = [lbl for val, lbl in W.AGE_GROUPS if val in ages] or ["All ages"]
    missing, sheets_written = [], []

    def raw(grouping, db):
        path = os.path.join(job.folder, "raw", f"{W._slug(cond['name'])}__{grouping}__{db}.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    for g in W.API_GROUPINGS:
        d77, d157 = raw(g["key"], "D77"), raw(g["key"], "D157")
        if d77 is None and d157 is None:
            missing.append(g["sheet"])
            continue
        rows = merge_years(d77 or [], d157 or [])
        note = None
        if g["key"] == "race":
            note = ("Race categories change in 2021: 1999-2020 use CDC's bridged race (4 groups: Asian or Pacific Islander combined); "
                    "2021+ use Single Race 6 (Asian and Native Hawaiian/Other Pacific Islander separate; 'More than one race' added). "
                    "Compare Asian/Pacific Islander trends across 2020-2021 with care.")
        if g["key"] == "age":
            note = "Age-adjusted rates don't apply when data are grouped by age - crude (age-specific) rates are shown."
        _write_sheet(wb, g["sheet"], None if g["key"] == "overall" else g["label"], rows, note)
        sheets_written.append(g["sheet"])

    for g in W.WEB_GROUPINGS:
        parts = {imp["db"]: imp["rows"] for imp in web_imports if imp["grouping"] == g["key"] and imp.get("condition") == cond["name"]}
        if not parts:
            missing.append(f"{g['sheet']} (export it from the CDC WONDER website)")
            continue
        rows = merge_years(parts.get("D77", []), parts.get("D157", []))
        note = None if len(parts) == 2 else f"Only the {W.DATASETS[list(parts)[0]]['short']} export was imported - the other years are missing."
        _write_sheet(wb, g["sheet"], g["label"], rows, note)
        sheets_written.append(g["sheet"])

    # About sheet
    a_name, b_name = cond.get("label_a", cond["name"]), cond.get("label_b")
    underlying = spec.get("cause", "multiple") == "underlying"
    if b_name:
        who = f"Deaths with BOTH {a_name} AND {b_name} on the same death certificate"
        codes_text = f"{a_name}: {', '.join(cond['codes'])}    AND    {b_name}: {', '.join(cond['and_codes'])}"
        cause_text = (f"{a_name} as the UNDERLYING cause of death, with {b_name} anywhere else on the certificate (contributing cause)"
                      if underlying else f"Both conditions anywhere on the death certificate (multiple cause of death)")
    else:
        who = f"Deaths with {a_name}"
        codes_text = ", ".join(cond["codes"])
        cause_text = "Underlying cause of death" if underlying else "Anywhere on the death certificate (multiple cause of death)"
    info = [
        ("Population studied", who),
        ("ICD-10 codes", codes_text),
        ("Cause of death", cause_text),
        ("Age groups", ", ".join(age_labels)),
        ("Years", f"1999-2020 from '{W.DATASETS['D77']['label']}' (D77); 2021-2024 from '{W.DATASETS['D157']['label']}' (D157). "
                  "2018-2020 appear in both datasets and are taken from D77 only."),
        ("Rates", "Per 100,000. Age-adjusted rates use the 2000 U.S. standard population, with 95% confidence intervals and standard errors (useful for Joinpoint)."),
        ("Sheets", ", ".join(sheets_written)),
        ("Not included", ", ".join(missing) if missing else "Nothing - all groupings included."),
        ("Suppressed / unreliable", "CDC hides counts of 1-9 deaths ('Suppressed') and flags rates from fewer than 20 deaths ('Unreliable'). "
                                    "Those rows are grey with a note."),
        ("Data use", "CDC WONDER data-use restrictions apply: never publish counts of 9 or fewer (or rates based on them), "
                     "and make no attempt to identify any person."),
        ("Source", "CDC WONDER, Multiple Cause of Death Files, National Center for Health Statistics. https://wonder.cdc.gov"),
        ("Extracted", time.strftime("%d %B %Y, %H:%M") + " by AURA"),
    ]
    about.column_dimensions["A"].width = 24
    about.column_dimensions["B"].width = 120
    about.cell(row=1, column=1, value=f"{cond['name']} - CDC WONDER mortality data").font = Font(bold=True, size=14, color="0B3A4A")
    for i, (k, v) in enumerate(info, 3):
        about.cell(row=i, column=1, value=k).font = Font(bold=True)
        cell = about.cell(row=i, column=2, value=v)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    path = os.path.join(job.folder, f"{W._slug(cond['name'])} - CDC WONDER.xlsx")
    wb.save(path)
    return path
