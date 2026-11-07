# Tests for AURA STUDIO (studio.py) and its phone routes - offline: the planner (LLM) is replaced with a fixed plan,
# so these check the pipeline itself: make -> version -> edit -> upload -> phone API.
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import phone_bridge
import studio


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = studio.STUDIO_DIR, studio.UPLOADS, studio.plan
        studio.STUDIO_DIR = self.dir
        studio.UPLOADS = os.path.join(self.dir, "_uploads")
        self.plans = []
        studio.plan = lambda request, base, atts: self.plans.pop(0)

    def tearDown(self):
        studio.STUDIO_DIR, studio.UPLOADS, studio.plan = self.old
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, plan, request="x", project=None, attachments=None):
        self.plans.append(plan)
        r = studio.wait(studio.start(request, project, attachments), 120)
        self.assertEqual(r["state"], "done", r.get("error"))
        return r

    def test_chart_then_edit_keeps_versions(self):
        chart = {"type": "pie", "title": "My week", "labels": ["Study", "Gym", "Sleep"], "series": [{"name": "h", "values": [30, 6, 49]}]}
        r = self.make({"kind": "chart", "title": "My week", "spec": chart, "message": "Here's your week."}, "pie chart of my week")
        self.assertTrue(os.path.isfile(r["file"]) and r["file"].endswith(".png"))
        r2 = self.make({"kind": "chart", "spec": {**chart, "type": "donut"}, "message": "Now a donut."}, "make it a donut", r["project"])
        self.assertEqual(r2["project"], r["project"])
        proj = studio.load(r["project"])
        self.assertEqual([v["v"] for v in proj["versions"]], [1, 2])
        self.assertEqual(proj["title"], "My week")                       # an edit keeps the name
        self.assertTrue(proj["versions"][1]["thumb"])
        self.assertTrue(os.path.isfile(os.path.join(studio._pdir(r["project"]), "v1.png")))   # v1 is never overwritten

    def test_edit_an_uploaded_photo(self):
        from PIL import Image
        src = os.path.join(self.dir, "me.png")
        Image.new("RGB", (400, 300), "#336699").save(src)
        with open(src, "rb") as f:
            up = studio.save_upload("me.png", f.read())
        self.assertEqual(up["kind"], "image")
        r = self.make({"kind": "photo_edit", "spec": {"ops": [{"op": "crop", "aspect": "1:1"}, {"op": "rotate", "deg": 90}]}, "message": "Cropped."},
                      "crop it square", attachments=[up["id"]])
        self.assertEqual(Image.open(r["file"]).size, (300, 300))

    def test_uploads_cannot_escape_the_folder(self):
        up = studio.save_upload("..\\..\\evil.txt", b"x")
        self.assertNotIn("..", up["id"])
        self.assertIsNone(studio.upload_path("../../Windows/win.ini"))

    def test_failure_is_reported_not_raised(self):
        self.plans.append({"kind": "photo_edit", "spec": {"ops": []}})
        r = studio.wait(studio.start("brighten it"), 60)
        self.assertEqual(r["state"], "error")
        self.assertIn("no picture", r["error"])
        with self.assertRaises(ValueError):
            studio.start("   ")

    def test_quote_labels(self):
        out = studio._quote_labels("flowchart TD\n A[Give adrenaline (IM)] --> B((Call 999))")
        self.assertIn('A["Give adrenaline (IM)"]', out)
        self.assertIn('B(("Call 999"))', out)

    def test_phone_routes(self):
        r = self.make({"kind": "qr", "title": "Wifi", "spec": {"data": "hello"}, "message": "QR ready."})
        dev = {"id": "test"}
        items = phone_bridge._studio("GET", "projects", {}, {}, dev)["items"]
        self.assertEqual(items[0]["id"], r["project"])
        self.assertTrue(items[0]["thumb"].startswith("/v/"))
        p = phone_bridge._studio("GET", "project", {"id": [r["project"]]}, {}, dev)
        self.assertTrue(p["versions"][0]["link"] and "spec" not in p["versions"][0])
        self.assertIn("error", phone_bridge._studio("GET", "project", {"id": ["nope"]}, {}, dev))
        self.assertIn("error", phone_bridge._studio("POST", "create", {}, {"prompt": ""}, dev))
        self.assertIn("error", phone_bridge._studio("POST", "hf", {}, {"token": "not-a-token"}, dev))
        self.assertTrue(phone_bridge._studio("POST", "delete", {}, {"id": r["project"]}, dev)["ok"])
        self.assertEqual(phone_bridge._studio("GET", "projects", {}, {}, dev)["items"], [])


if __name__ == "__main__":
    unittest.main()
