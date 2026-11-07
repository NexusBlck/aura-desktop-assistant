# Tests for AURA SPATIAL (spatial.py) - offline: file safety (types by bytes, zip / glTF path tricks), the scientific parsers,
# the structural-model cleaner, the honest fallback ladder and the phone routes.
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import phone_bridge
import spatial

SDF = """caffeine
  test

  3  2  0     0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
   -0.7000    0.9000    0.0000 N   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  2  0  0  0  0
  1  3  1  0  0  0  0
M  END
$$$$
"""


class FileSafety(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_types_come_from_bytes_not_names(self):
        self.assertEqual(spatial.sniff(self.write("a.bin", b"glTF\x02\x00\x00\x00" + b"\x00" * 20)), "glb")
        self.assertEqual(spatial.sniff(self.write("b.glb", b"solid x\nfacet normal 0 0 1\n")), "stl")
        tri = struct.pack("<I", 1) + b"\x00" * 50
        self.assertEqual(spatial.sniff(self.write("c.dat", b"\x00" * 80 + tri)), "stl")
        self.assertEqual(spatial.sniff(self.write("d.txt", b"ply\nformat ascii 1.0\n")), "ply")
        self.assertIsNone(spatial.sniff(self.write("evil.glb", b"MZ\x90\x00 this is an exe")))
        self.assertIsNone(spatial.sniff(self.write("e.glb", b"<script>alert(1)</script>")))

    def test_zip_keeps_only_models_and_stays_inside(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../../escape.glb", b"glTF....")
            z.writestr("scene/scene.gltf", json.dumps({"asset": {"version": "2.0"}, "buffers": [{"uri": "scene.bin"}]}))
            z.writestr("scene/scene.bin", b"\x00" * 10)
            z.writestr("scene/run.exe", b"MZ")
            z.writestr("scene/textures/a.png", b"\x89PNG")
        zp = self.write("m.zip", buf.getvalue())
        out = os.path.join(self.d, "out")
        os.makedirs(out)
        main = spatial.safe_unzip(zp, out)
        self.assertFalse(os.path.exists(os.path.join(self.d, "escape.glb")))
        self.assertTrue(main.startswith(os.path.realpath(out)))
        found = [os.path.relpath(os.path.join(r, f), out) for r, _d, fs in os.walk(out) for f in fs]
        self.assertNotIn(os.path.join("scene", "run.exe"), found)
        self.assertIn(os.path.join("scene", "textures", "a.png"), found)

    def test_gltf_cannot_reach_outside(self):
        ok = self.write("ok.gltf", json.dumps({"asset": {}, "buffers": [{"uri": "a.bin"}], "images": [{"uri": "data:image/png;base64,AAA"}]}).encode())
        self.assertTrue(spatial._safe_gltf(ok))
        for uri in ("https://evil.example/x.bin", "../secret.bin", "/etc/passwd", "file:///C:/x", "C:\\\\Windows\\\\x"):
            bad = self.write("bad.gltf", json.dumps({"asset": {}, "buffers": [{"uri": uri}]}).encode())
            with self.assertRaises(RuntimeError, msg=uri):
                spatial._safe_gltf(bad)

    def test_downloads_only_from_the_sources_servers(self):
        self.assertTrue(spatial._host_ok("smithsonian", "https://3d-api.si.edu/content/document/x.glb"))
        self.assertTrue(spatial._host_ok("nasa", "https://raw.githubusercontent.com/nasa/NASA-3D-Resources/master/a.glb"))
        self.assertFalse(spatial._host_ok("nasa", "https://raw.githubusercontent.com/someone/else/a.glb"))
        self.assertFalse(spatial._host_ok("smithsonian", "http://3d-api.si.edu/x.glb"))
        self.assertFalse(spatial._host_ok("wikimedia", "https://127.0.0.1/x.stl"))

    def test_library_choice_cannot_leave_the_library(self):
        old = spatial.LIBRARY
        spatial.LIBRARY = os.path.join(self.d, "lib")
        os.makedirs(spatial.LIBRARY)
        try:
            with self.assertRaises(RuntimeError):
                spatial.LocalAssetProvider().fetch({"dir": os.environ.get("WINDIR", "C:\\Windows"), "entry": "win.ini"}, spatial.Job("x"))
            with self.assertRaises(RuntimeError):
                spatial.LocalAssetProvider().fetch({"dir": spatial.LIBRARY, "entry": "..\\..\\x.glb"}, spatial.Job("x"))
            p = os.path.join(spatial.LIBRARY, "heart.stl")
            with open(p, "wb") as f:
                f.write(b"solid h\nfacet normal 0 0 1\n")
            r = spatial.LocalAssetProvider().fetch({"dir": spatial.LIBRARY, "entry": "heart.stl", "provider": "library"}, spatial.Job("x"))
            self.assertEqual(r["format"], "stl")
        finally:
            spatial.LIBRARY = old


class Science(unittest.TestCase):
    def test_sdf(self):
        atoms, bonds = spatial.parse_sdf(SDF)
        self.assertEqual([a[0] for a in atoms], ["C", "O", "N"])
        self.assertEqual(bonds, [[0, 1, 2], [0, 2, 1]])


class StructuralModels(unittest.TestCase):
    def test_clean_spec_keeps_sane_parts_only(self):
        spec = {"title": "Heart", "parts": [
            {"name": "Left Ventricle", "shape": "ellipsoid", "dims": {"rx": 3, "ry": "4", "rz": 2.5}, "pos": [1, 0, 0], "color": "#aa2233"},
            {"name": "Left Ventricle", "shape": "sphere", "dims": {"r": 1}},
            {"name": "evil", "shape": "script", "dims": {}},
            {"name": "Aorta", "shape": "tube", "dims": {"points": [[0, 0, 0], [0, 5, 1], "x"], "r": 1}, "color": "red",
             "copies": [{"name": "Aorta", "pos": [2, 0, 0]}]},
            {"name": "Surface", "shape": "parametric", "dims": {"x": "cos(u)" * 100, "u": [0, 6.28]}},
        ], "animations": [{"name": "beat", "type": "pulse", "parts": ["Left Ventricle"], "speed": 99}, {"type": "explode"}]}
        out = spatial.clean_spec(spec)
        names = [p["name"] for p in out["parts"]]
        self.assertEqual(names[:2], ["Left Ventricle", "Left Ventricle 2"])
        self.assertNotIn("evil", names)
        aorta = next(p for p in out["parts"] if p["name"] == "Aorta")
        self.assertEqual(aorta["color"], "#9ec9d9")                       # not a hex colour -> default
        self.assertEqual(len(aorta["dims"]["points"]), 2)
        self.assertNotEqual(aorta["copies"][0]["name"].lower(), "aorta")   # copies get unique names
        self.assertLessEqual(len(next(p for p in out["parts"] if p["shape"] == "parametric")["dims"]["x"]), 200)
        self.assertEqual(len(out["animations"]), 1)
        self.assertEqual(out["animations"][0]["speed"], 20)


class Ladder(unittest.TestCase):
    """With every source empty and the AI unreachable, AURA must say so honestly (level 7) - never fake a model."""

    def test_nothing_found_is_level_7_not_a_fake(self):
        saved = (spatial.understand, spatial.gather, spatial.PROCEDURAL.fetch, spatial.IMAGE.fetch, spatial.GENERATED.available)
        spatial.understand = lambda q, c=None: {"subject": "zorblax", "display_name": "Zorblax", "query": q, "search_terms": ["zorblax"],
                                                "science": {"type": "none"}, "expected_parts": [], "concrete": True}
        spatial.gather = lambda u, job=None: ([], {})
        spatial.GENERATED.available = lambda: False

        def fail(*a, **k):
            raise RuntimeError("offline")
        spatial.PROCEDURAL.fetch = fail
        spatial.IMAGE.fetch = fail
        try:
            scene = spatial.resolve("bring up a zorblax", spatial.Job("t"))
            self.assertEqual(scene["level"], 7)
            self.assertEqual(scene["kind"], "none")
            self.assertTrue(scene["tried"])
            self.assertIn("couldn't", scene["message"].lower())
        finally:
            (spatial.understand, spatial.gather, spatial.PROCEDURAL.fetch, spatial.IMAGE.fetch, spatial.GENERATED.available) = saved

    def test_unrelated_candidates_are_never_used(self):
        saved = (spatial.understand, spatial.gather, spatial.rank, spatial.PROCEDURAL.fetch, spatial.GENERATED.available)
        spatial.understand = lambda q, c=None: {"subject": "human heart", "display_name": "Human Heart", "query": q, "search_terms": ["heart"],
                                                "science": {"type": "none"}, "expected_parts": [], "concrete": True, "aspect": "whole"}
        cand = {"provider": "smithsonian", "provider_label": "Smithsonian 3D", "id": "pkg", "title": "Heart-shaped locket", "url": "https://3d-api.si.edu/x.glb"}
        spatial.gather = lambda u, job=None: ([cand], {})
        spatial.rank = lambda u, c: [dict(cand, score=2.0)]
        spatial.GENERATED.available = lambda: False
        spatial.PROCEDURAL.fetch = lambda c, job: {"kind": "procedural", "spec": {"parts": []}, "source": {"label": "AI-built structural model"}}
        try:
            scene = spatial.resolve("bring up a human heart", spatial.Job("t"))
            self.assertEqual(scene["kind"], "procedural")                 # the locket (score 2) was rejected
            self.assertEqual(scene["level"], 5)
        finally:
            (spatial.understand, spatial.gather, spatial.rank, spatial.PROCEDURAL.fetch, spatial.GENERATED.available) = saved


class Routes(unittest.TestCase):
    def test_scene_links_never_send_pc_paths(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "model.glb"), "wb") as f:
                f.write(b"glTF")
            s = phone_bridge._scene_links({"kind": "model", "dir": d, "entry": "model.glb", "alternatives": [{"provider": "library", "dir": d, "title": "x"}]})
            self.assertNotIn("dir", s)
            self.assertTrue(s["url"].startswith("/v/") and s["url"].endswith("/model.glb"))
            self.assertNotIn("dir", s["alternatives"][0])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_settings_validate_token(self):
        self.assertIn("error", phone_bridge._spatial("POST", "settings", {}, {"sketchfab_token": "not a token!"}, {"id": "t"}))
        self.assertIn("error", phone_bridge._spatial("POST", "resolve", {}, {"query": ""}, {"id": "t"}))


if __name__ == "__main__":
    unittest.main()
