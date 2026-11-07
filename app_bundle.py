# app_bundle.py
# Packs AURA Mobile (mobile/src/**/*.js + the CSS + the HUD cat) into ONE download, so the phone never has
# to fetch ~35 separate files over the VPN to start. The phone keeps the pack in its own storage and starts
# from it (mobile/boot.js) - opening AURA doesn't depend on the network; updates arrive in the background.
#
# The app's modules are plain ES modules using only: `import {a, b as c} from './x.js'`,
# `import * as x from './x.js'`, `export function|async function|const|let|class name`, and `import('./x.js')`.
# Each becomes a function in a tiny module registry (exports are live getters, so order works like ES modules).
import hashlib
import json
import os
import posixpath
import re
import threading

AURA_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(AURA_DIR, "mobile")
SRC_DIR = os.path.join(APP_DIR, "src")
CSS_FILES = ["src/ui/tokens.css", "src/ui/base.css", "src/ui/components.css", "src/ui/vision.css", "src/ui/nexus.css", "src/ui/spatial.css", "src/ui/tablet.css",
             "src/ui/skins/base.css", "src/ui/skins/ledger.css", "src/ui/skins/hmi.css", "src/ui/skins/swiss.css",
             "src/ui/skins/console.css", "src/ui/skins/haven.css", "src/ui/skins/picker.css"]
ENTRY = "src/app.js"
SKIP = {"src/errors.js"}          # loaded on its own, before anything else (it reports / repairs startup problems)

_lock = threading.Lock()
_cache = {"stamp": None, "bundle": None}

IMPORT_RE = re.compile(r"^[ \t]*import\s+(\{[^}]*\}|\*\s+as\s+(\w+))\s+from\s+'([^']+)'\s*;?[ \t]*$", re.M)
EXPORT_RE = re.compile(r"^([ \t]*)export\s+((?:async\s+)?function\*?|const|let|class)\s+(\w+)", re.M)
DYN_IMPORT_RE = re.compile(r"\bimport\(\s*'([^']+)'\s*\)")


def _files():
    out = []
    for root, _dirs, names in os.walk(SRC_DIR):
        for n in names:
            if n.endswith(".js"):
                rel = os.path.relpath(os.path.join(root, n), APP_DIR).replace("\\", "/")
                if rel not in SKIP:
                    out.append(rel)
    return sorted(out)


def _stamp():
    paths = [os.path.join(APP_DIR, f) for f in _files() + CSS_FILES] + [os.path.join(AURA_DIR, "Face", "cat.js"), __file__]
    return tuple((p, os.path.getmtime(p)) for p in paths if os.path.exists(p))


def _resolve(from_mod, spec):
    if not spec.startswith("."):
        raise ValueError(f"{from_mod}: only relative imports are supported ({spec})")
    return posixpath.normpath(posixpath.join(posixpath.dirname(from_mod), spec))


def _transform(mod, code):
    names = []

    def imp(m):
        what, ns, spec = m.group(1), m.group(2), m.group(3)
        target = _resolve(mod, spec)
        if ns:
            return f"const {ns} = __req({json.dumps(target)});"
        inner = what.strip()[1:-1]
        parts = []
        for piece in inner.split(","):
            piece = piece.strip()
            if not piece:
                continue
            m2 = re.match(r"^(\w+)(?:\s+as\s+(\w+))?$", piece)
            if not m2:
                raise ValueError(f"{mod}: can't read import '{piece}'")
            parts.append(f"{m2.group(1)}: {m2.group(2)}" if m2.group(2) else m2.group(1))
        return f"const {{ {', '.join(parts)} }} = __req({json.dumps(target)});"

    def exp(m):
        names.append(m.group(3))
        return f"{m.group(1)}{m.group(2)} {m.group(3)}"

    code = IMPORT_RE.sub(imp, code)
    code = EXPORT_RE.sub(exp, code)
    code = DYN_IMPORT_RE.sub(lambda m: f"__import({json.dumps(_resolve(mod, m.group(1)))})", code)
    if re.search(r"^\s*(import|export)\b", code, re.M):
        raise ValueError(f"{mod}: an import/export the packer doesn't understand is left")
    getters = ", ".join(f"{n}: () => {n}" for n in dict.fromkeys(names))
    return (f"__defs[{json.dumps(mod)}] = function (__exports) {{\n'use strict';\n"
            f"__bind(__exports, {{ {getters} }});\n{code}\n}};\n")


def build():
    parts = ["(function () {\n'use strict';\n",
             "const __defs = {}, __cache = {};\n",
             "function __bind(ex, g) { for (const k in g) Object.defineProperty(ex, k, { get: g[k], enumerable: true }); }\n",
             "function __req(n) { if (__cache[n]) return __cache[n]; const f = __defs[n]; if (!f) throw new Error('AURA: missing module ' + n);"
             " const ex = {}; __cache[n] = ex; f(ex); return ex; }\n",
             "function __import(n) { return new Promise((res, rej) => { try { res(__req(n)); } catch (e) { rej(e); } }); }\n"]
    for mod in _files():
        with open(os.path.join(APP_DIR, mod), encoding="utf-8") as f:
            parts.append(f"// ---- {mod}\n" + _transform(mod, f.read()))
    parts.append(f"__req({json.dumps(ENTRY)});\n}})();\n")
    with open(os.path.join(AURA_DIR, "Face", "cat.js"), encoding="utf-8") as f:
        js = "// ---- the HUD cat (Face/cat.js)\n" + f.read() + "\n" + "".join(parts)
    css = ""
    for c in CSS_FILES:
        with open(os.path.join(APP_DIR, c), encoding="utf-8") as f:
            css += f"/* ---- {c} */\n" + f.read() + "\n"
    version = hashlib.sha1((js + css).encode("utf-8")).hexdigest()[:16]
    return {"version": version, "js": js, "css": css}


def get():
    """The current pack (rebuilt only when a file changed)."""
    with _lock:
        stamp = _stamp()
        if _cache["stamp"] != stamp:
            _cache["bundle"] = build()
            _cache["stamp"] = stamp
        return _cache["bundle"]
