# cdc_wonder.py
# Pulls mortality data from CDC WONDER (Multiple Cause of Death) for research
# and turns it into clean Excel workbooks.
#
# How it works:
#  - National groupings (Year, Sex, Race, Hispanic origin, Age, Place of
#    death - each broken down by year) come automatically through CDC's
#    official API, from BOTH datasets: 1999-2020 (D77) and 2018-2024 (D157).
#  - CDC does not allow location data (Census region, State, Urbanization)
#    through the API, so those are exported by you from the CDC WONDER website
#    (AURA gives a checklist) and AURA picks the files up from Downloads.
#  - Everything is merged into one workbook per condition: 1999-2020 from
#    D77, 2021 onward from D157 (the overlapping 2018-2020 rows are dropped).
#
# CDC's rules followed here: queries run one at a time, 2 minutes apart (their
# guidance for automated use), and every request carries your acceptance of
# their data-use restrictions (asked once in the extractor window).
import os
import re
import json
import time
import threading
from xml.sax.saxutils import escape
import requests

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(AURA_DIR, "cdc_wonder_templates.json")
CONSENT_FILE = os.path.join(AURA_DIR, "aura_cdc_consent.json")
OUTPUT_ROOT = os.path.join(os.path.expanduser("~"), "Documents", "AURA CDC WONDER")
API_URL = "https://wonder.cdc.gov/controller/datarequest/{db}"
QUERY_GAP_SECONDS = 120  # CDC: "Firing a query every 2 minutes provides good recovery time of our system."

DATASETS = {
    "D77": {"label": "Multiple Cause of Death, 1999-2020", "short": "1999-2020", "years": (1999, 2020),
            "race_var": "V8", "race_label": "Race (bridged, 4 groups)"},
    "D157": {"label": "Multiple Cause of Death, 2018-2024, Single Race", "short": "2018-2024", "years": (2018, 2024),
             "race_var": "V42", "race_label": "Single Race 6"},
}
# Which dataset each year comes from in the merged sheets (no repeated years).
PRIMARY_UNTIL = 2020  # 1999-2020 from D77, 2021+ from D157

# Ten-year age groups exactly as on the CDC WONDER form (value, label).
AGE_GROUPS = [
    ("1", "< 1 year"), ("1-4", "1-4 years"), ("5-14", "5-14 years"), ("15-24", "15-24 years"),
    ("25-34", "25-34 years"), ("35-44", "35-44 years"), ("45-54", "45-54 years"), ("55-64", "55-64 years"),
    ("65-74", "65-74 years"), ("75-84", "75-84 years"), ("85+", "85+ years"), ("NS", "Not Stated"),
]

# Groupings pulled automatically (national). Each is also broken down by year.
API_GROUPINGS = [
    {"key": "overall", "sheet": "Overall", "label": "All deaths (by year)", "var": None, "aar": True},
    {"key": "sex", "sheet": "Sex", "label": "Sex", "var": "V7", "aar": True},
    {"key": "race", "sheet": "Race", "label": "Race", "var": "RACE", "aar": True},
    {"key": "hispanic", "sheet": "Hispanic Origin", "label": "Hispanic origin", "var": "V17", "aar": True},
    {"key": "age", "sheet": "Age Group", "label": "Ten-year age group", "var": "V5", "aar": False},  # CDC can't age-adjust when grouped by age
    {"key": "place", "sheet": "Place of Death", "label": "Place of death", "var": "V21", "aar": True},
]
# Groupings CDC only allows on the website (you export them; AURA imports them).
WEB_GROUPINGS = [
    {"key": "census", "sheet": "Census Region", "label": "Census Region", "form_label": "Census Region", "var": "V10-level1"},
    {"key": "state", "sheet": "State", "label": "State", "form_label": "State", "var": "V9-level1"},
    {"key": "urban", "sheet": "Urbanization", "label": "2013 Urbanization", "form_label": "2013 Urbanization", "var": "V19"},
]
WEBSITE_URLS = {"D77": "https://wonder.cdc.gov/mcd-icd10.html", "D157": "https://wonder.cdc.gov/mcd-icd10-expanded.html"}


# ------------------------------------------------------------------ consent
def has_consent():
    try:
        with open(CONSENT_FILE, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("accepted"))
    except (OSError, ValueError):
        return False


def record_consent():
    with open(CONSENT_FILE, "w", encoding="utf-8") as f:
        json.dump({"accepted": True, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "terms": "CDC WONDER / NCHS data use restrictions"}, f)


# ------------------------------------------------------------------ ICD codes
_CODE = re.compile(r"^[A-Z]\d{2}(\.\d{1,2})?$")


def parse_icd_codes(text):
    """'I50, I20-I25; M15.0' -> (['I50','I20','I21',...,'I25','M15.0'], errors).
    Ranges of 3-character codes with the same letter are expanded."""
    codes, errors = [], []
    for token in re.split(r"[,\s;]+", (text or "").upper().strip()):
        if not token:
            continue
        m = re.fullmatch(r"([A-Z])(\d{2})-(?:([A-Z]))?(\d{2})", token)
        if m:
            letter, a, letter2, b = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4))
            if letter2 != letter or b < a:
                errors.append(f"{token} (ranges must stay within one letter, low to high)")
                continue
            codes += [f"{letter}{n:02d}" for n in range(a, b + 1)]
        elif _CODE.match(token):
            codes.append(token)
        else:
            errors.append(token)
    return list(dict.fromkeys(codes)), errors


# ------------------------------------------------------------------ requests
_templates = None


def _template(db):
    global _templates
    if _templates is None:
        with open(TEMPLATES, "r", encoding="utf-8") as f:
            _templates = json.load(f)
    params = {}
    for k, v in _templates[db]:
        params.setdefault(k, []).append(v)
    return params


def build_request(db, grouping_var, codes, ages, cause="multiple", with_aar=True, and_codes=None):
    """The request XML for one query: grouping_var by Year, restricted to the
    ICD codes and age groups chosen."""
    p = _template(db)

    def put(k, v):
        p[k] = v if isinstance(v, list) else [v]

    var = DATASETS[db]["race_var"] if grouping_var == "RACE" else grouping_var
    if var:
        put("B_1", f"{db}.{var}")
        put("B_2", f"{db}.V1-level1")
    else:
        put("B_1", f"{db}.V1-level1")
        put("B_2", "*None*")
    for k in ("B_3", "B_4", "B_5"):
        put(k, "*None*")

    # rates: age-adjusted (2000 US standard) with 95% CI and standard error
    if with_aar:
        put("O_aar", "aar_std"); put("O_aar_enable", "true"); put("O_aar_CI", "true"); put("O_aar_SE", "true")
    else:
        put("O_aar", "aar_none")
    put("O_show_totals", "false"); put("O_show_zeros", "true"); put("O_show_suppressed", "true")
    put("O_precision", "2")

    # ages (ten-year groups)
    put("O_age", f"{db}.V5")
    put(f"V_{db}.V5", ages if ages else ["*All*"])

    # cause of death
    #   multiple  : A anywhere on the certificate (AND B anywhere, if given)
    #   underlying: A is the underlying cause (AND B anywhere, if given - i.e. died FROM A, WITH B)
    if cause == "underlying":
        put("O_ucd", f"{db}.V2"); put("O_V2_fmode", "freg"); put(f"F_{db}.V2", codes); put(f"V_{db}.V2", [""])
        if and_codes:
            put("O_mcd", f"{db}.V13"); put("O_V13_fmode", "fadv")
            put(f"V_{db}.V13", and_codes); put(f"F_{db}.V13", and_codes)
    else:
        put("O_mcd", f"{db}.V13"); put("O_V13_fmode", "fadv")
        put(f"V_{db}.V13", codes); put(f"F_{db}.V13", codes)
        if and_codes:
            put(f"V_{db}.V13_AND", and_codes)

    put("accept_datause_restrictions", "true")
    parts = ["<request-parameters>"]
    for k, vals in p.items():
        parts.append(f"<parameter><name>{escape(k)}</name>" + "".join(f"<value>{escape(str(v))}</value>" for v in vals) + "</parameter>")
    parts.append("</request-parameters>")
    return "".join(parts)


def _num(text):
    t = (text or "").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _ci(cell):
    m = re.search(r'<l v="\(([\d.,]+)\s*-\s*([\d.,]+)\)"', cell)
    return (_num(m.group(1)), _num(m.group(2))) if m else (None, None)


def parse_response(xml, db, grouped):
    """CDC's XML reply -> list of row dicts. Reads the column list CDC sends
    (measure-selections) so it never depends on column positions."""
    msgs = [re.sub(r"\s+", " ", m).strip() for m in re.findall(r"<message[^>]*>(.*?)</message>", xml, re.S)]
    table = re.search(r"<data-table.*?</data-table>", xml, re.S)
    if not table:
        raise RuntimeError("CDC WONDER returned no data" + (f": {msgs[0]}" if msgs else "."))
    measures = re.findall(r'<measure code="[^.]+\.(M\d+)"', re.search(r"<measure-selections>(.*?)</measure-selections>", xml, re.S).group(1))
    # CI measures (M32/M33 crude, M42/M43 age-adjusted) ride inside their rate's cell, not in their own column.
    columns = [m for m in measures if m not in ("M32", "M33", "M42", "M43")]
    n_labels = 2 if grouped else 1
    rows, carry = [], [None] * n_labels
    for r in re.findall(r"<r>(.*?)</r>", table.group(0), re.S):
        cells = re.findall(r"<c\b[^>]*?(?:/>|>.*?</c>)", r, re.S)
        labels = [re.search(r'\bl="([^"]*)"', c) for c in cells if re.search(r'<c\b[^>]*\bl="', c)]
        labels = [m.group(1).strip() for m in labels if m]
        values = [c for c in cells if not re.search(r'<c\b[^>]*\bl="', c)]
        # a label with r="N" spans the following rows - carry it forward
        if len(labels) == n_labels:
            carry = labels
        elif len(labels) < n_labels:
            carry = carry[: n_labels - len(labels)] + labels
        group, year = (carry[0], carry[1]) if grouped else ("All", carry[0])
        row = {"group": group, "year": int(re.sub(r"\D", "", year) or 0), "dataset": db, "flags": []}
        for code, cell in zip(columns, values):
            raw = re.search(r'\bv="([^"]*)"', cell)
            raw = raw.group(1) if raw else ""
            if raw.lower() in ("suppressed", "unreliable", "not applicable", "missing"):
                row["flags"].append(f"{code}:{raw.lower()}")
            val = _num(raw)
            if code == "M1": row["deaths"] = val if val is not None else raw
            elif code == "M2": row["population"] = val
            elif code == "M3": row["crude"] = val; row["crude_lo"], row["crude_hi"] = _ci(cell)
            elif code == "M31": row["crude_se"] = val
            elif code == "M4": row["aar"] = val; row["aar_lo"], row["aar_hi"] = _ci(cell)
            elif code == "M41": row["aar_se"] = val
        rows.append(row)
    return rows


def run_query(db, grouping_var, codes, ages, cause, with_aar, and_codes=None):
    xml = build_request(db, grouping_var, codes, ages, cause, with_aar, and_codes)
    resp = requests.post(API_URL.format(db=db), data={"request_xml": xml, "accept_datause_restrictions": "true"}, timeout=600)
    if resp.status_code != 200:
        raise RuntimeError(f"CDC WONDER answered HTTP {resp.status_code}")
    return parse_response(resp.text, db, grouped=bool(grouping_var))


# ------------------------------------------------------------------ jobs
def _slug(text):
    return re.sub(r'[<>:"/\\|?*]+', "", text).strip()[:60] or "condition"


class Job:
    """One extraction: one or more conditions, each queried for every API
    grouping in both datasets. Progress is saved to disk after every query,
    so an interrupted job resumes where it stopped."""

    def __init__(self, spec):
        self.spec = spec
        self.id = spec.get("id") or time.strftime("%Y%m%d-%H%M%S")
        self.folder = os.path.join(OUTPUT_ROOT, self.id)
        os.makedirs(os.path.join(self.folder, "raw"), exist_ok=True)
        self.queries = []
        for cond in spec["conditions"]:
            for g in API_GROUPINGS:
                for db in DATASETS:
                    self.queries.append({"condition": cond["name"], "grouping": g["key"], "db": db,
                                         "file": os.path.join(self.folder, "raw", f"{_slug(cond['name'])}__{g['key']}__{db}.json"),
                                         "status": "queued", "error": None})
        for q in self.queries:
            if os.path.exists(q["file"]):
                q["status"] = "done"
        self.state = "ready"
        self.next_query_at = None
        self.started_at = time.time()
        self.outputs = []
        self._stop = False
        with open(os.path.join(self.folder, "job.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2)

    def condition(self, name):
        return next(c for c in self.spec["conditions"] if c["name"] == name)

    def start(self, on_done=None):
        self.state = "running"
        threading.Thread(target=self._run, args=(on_done,), daemon=True).start()

    def stop(self):
        self._stop = True

    def _run(self, on_done):
        last = 0
        for q in self.queries:
            if self._stop:
                self.state = "stopped"
                return
            if q["status"] == "done":
                continue
            wait = QUERY_GAP_SECONDS - (time.time() - last)
            if last and wait > 0:
                self.next_query_at = time.time() + wait
                while time.time() < self.next_query_at:
                    if self._stop:
                        self.state = "stopped"
                        return
                    time.sleep(0.5)
            self.next_query_at = None
            cond = self.condition(q["condition"])
            g = next(g for g in API_GROUPINGS if g["key"] == q["grouping"])
            q["status"] = "running"
            for attempt in range(3):
                try:
                    rows = run_query(q["db"], g["var"], cond["codes"], self.spec.get("ages"), self.spec.get("cause", "multiple"),
                                     g["aar"], cond.get("and_codes"))
                    with open(q["file"], "w", encoding="utf-8") as f:
                        json.dump(rows, f)
                    q["status"], q["error"] = "done", None
                    break
                except Exception as e:
                    q["error"] = str(e)
                    if "returned no data" in str(e) or attempt == 2:
                        q["status"] = "failed"
                        break
                    time.sleep(30 * (attempt + 1))  # transient network problem - back off, then retry
            last = time.time()
        self.state = "api_done"
        if on_done:
            try:
                on_done(self)
            except Exception as e:
                print(f"(cdc wonder: on_done failed: {e})")

    def progress(self):
        done = sum(q["status"] == "done" for q in self.queries)
        remaining = sum(q["status"] in ("queued", "running") for q in self.queries)
        return {"id": self.id, "state": self.state, "done": done, "total": len(self.queries),
                "failed": [f"{q['condition']} · {q['grouping']} · {DATASETS[q['db']]['short']}: {q['error']}" for q in self.queries if q["status"] == "failed"],
                "running": next((f"{q['condition']} · {q['grouping']} · {DATASETS[q['db']]['short']}" for q in self.queries if q["status"] == "running"), None),
                "next_in": max(0, int(self.next_query_at - time.time())) if self.next_query_at else 0,
                "eta_minutes": round(remaining * QUERY_GAP_SECONDS / 60), "folder": self.folder, "outputs": self.outputs}
