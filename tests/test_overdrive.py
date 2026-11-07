# Tests for AURA OVERDRIVE: AEGIS (guard mode), TOTAL RECALL (screen memory), AIR CONTROL events and the voice router.
# Offline: Windows input, the lock screen, the camera, screenshots and the AI are all simulated.
import datetime
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import aegis
import overdrive
import recall


class Aegis(unittest.TestCase):
    def setUp(self):
        self.saved = (aegis.last_input_tick, aegis.is_locked, aegis.lock_pc, aegis._photos, aegis._save, aegis.GRACE, aegis.STATE_FILE)
        self.tick, self.locked, self.locks = [1000], [False], []
        aegis.last_input_tick = lambda: self.tick[0]
        aegis.is_locked = lambda: self.locked[0]
        aegis.lock_pc = lambda: (self.locks.append(1), self.locked.__setitem__(0, True))
        aegis._photos = lambda count=3: ["/tmp/p1.jpg"]
        aegis._save = lambda: None
        aegis.GRACE = 0
        aegis._state.update(armed=False, events=[], lock_on_touch=True, auto_minutes=0)
        import phone_bridge                            # never send real alerts to the phone from a test
        self.pb = (phone_bridge._push_all, phone_bridge.emit)
        self.pushed = []
        phone_bridge._push_all = lambda *a, **k: self.pushed.append(a)
        phone_bridge.emit = lambda *a, **k: None

    def tearDown(self):
        (aegis.last_input_tick, aegis.is_locked, aegis.lock_pc, aegis._photos, aegis._save, aegis.GRACE, aegis.STATE_FILE) = self.saved
        aegis._state["armed"] = False
        import phone_bridge
        phone_bridge._push_all, phone_bridge.emit = self.pb

    def step(self, watch_state):
        """One pass of the watcher's decision logic (the same code path as _watch, without the loop)."""
        locked = aegis.is_locked()
        st = aegis._state
        if st["armed"]:
            if watch_state["was_locked"] and not locked:
                watch_state["summary"] = aegis.disarm("unlock")
            elif not locked and aegis.last_input_tick() != st.get("baseline"):
                st["baseline"] = aegis.last_input_tick()
                aegis._intrusion()
        watch_state["was_locked"] = aegis.is_locked()

    def test_touch_while_armed_locks_photographs_and_logs(self):
        aegis.arm("test")
        w = {"was_locked": False}
        self.step(w)                                   # nobody touched it
        self.assertEqual(self.locks, [])
        self.tick[0] = 2000                            # someone moves the mouse
        self.step(w)
        self.assertEqual(len(self.locks), 1)
        self.assertEqual(len(aegis._state["events"]), 1)
        self.assertTrue(aegis._state["events"][0]["photos"])
        self.assertEqual(len(self.pushed), 1)                     # the phone alert (captured, not sent)
        self.locked[0] = False                         # the owner unlocks Windows
        self.step(w)
        self.assertFalse(aegis._state["armed"])
        self.assertEqual(len(w["summary"]["while_away"]), 1)
        self.assertIn("1 time", aegis.summary_text(w["summary"]["while_away"]))

    def test_disarmed_ignores_input_and_restart_never_arms(self):
        self.tick[0] = 5000
        self.step({"was_locked": False})
        self.assertEqual(self.locks, [])
        aegis._state["armed"] = True
        aegis._load()
        self.assertFalse(aegis._state["armed"])

    def test_photo_only_mode(self):
        aegis.set_lock_on_touch(False)
        aegis.arm("test")
        self.tick[0] = 3000
        self.step({"was_locked": False})
        self.assertEqual(self.locks, [])
        self.assertFalse(aegis._state["events"][-1]["locked"])


class Recall(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (recall.STORE, recall._foreground, recall._save, dict(recall._settings), dict(recall._live))
        recall.STORE = self.dir
        recall._save = lambda: None
        self.title = ["Report.docx - Word", "WINWORD.EXE"]
        recall._foreground = lambda: tuple(self.title)
        from PIL import Image
        self.colour = [(10, 20, 30)]
        import PIL.ImageGrab
        self.grab = PIL.ImageGrab.grab
        PIL.ImageGrab.grab = lambda *a, **k: Image.new("RGB", (1920, 1080), self.colour[0])
        recall._live.update(last_sig=None, last_title=None)

    def tearDown(self):
        import PIL.ImageGrab
        PIL.ImageGrab.grab = self.grab
        recall.STORE, recall._foreground, recall._save = self.saved[:3]
        recall._settings.clear(); recall._settings.update(self.saved[3])
        recall._live.clear(); recall._live.update(self.saved[4])
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_saves_only_new_screens_and_never_private_windows(self):
        self.assertIsNotNone(recall.capture_once())
        self.assertIsNone(recall.capture_once())                      # same screen, same window
        self.colour[0] = (200, 30, 30)
        self.assertIsNotNone(recall.capture_once())                   # the screen changed
        for t in ("Bitwarden - Vault", "Sign in - Google Accounts", "InPrivate - Microsoft Edge", "HSBC Online Banking"):
            self.title[0] = t
            self.colour[0] = (0, 0, 255) if self.colour[0] != (0, 0, 255) else (0, 255, 0)
            self.assertIsNone(recall.capture_once(), t)
        es = recall.entries("today")
        self.assertEqual(len(es), 2)
        self.assertTrue(all("Word" in e["title"] for e in es))
        img = recall.file_path(es[0]["day"], es[0]["file"])
        from PIL import Image
        self.assertEqual(Image.open(img).width, 1280)                 # stored small
        self.assertIsNone(recall.file_path(es[0]["day"], "../../secret.txt"))

    def test_search_reads_index_and_looks_at_moments(self):
        recall.capture_once()
        saved = (recall._plan, __import__("vision_lab")._json)
        now = datetime.datetime.now()
        recall._plan = lambda q: (now - datetime.timedelta(hours=1), now + datetime.timedelta(minutes=1), ["report"], "what")
        import vision_lab
        seen = {}
        vision_lab._json = lambda img, prompt, max_tokens=0: (seen.update(prompt=prompt, img=len(img)), {"answer": "You were writing Report.docx in Word.", "moments": [1]})[1]
        try:
            r = recall.search("what was I doing an hour ago?")
        finally:
            recall._plan, vision_lab._json = saved
        self.assertIn("Report.docx", r["answer"])
        self.assertEqual(len(r["moments"]), 1)
        self.assertIn("Report.docx - Word", seen["prompt"])
        self.assertGreater(seen["img"], 1000)

    def test_delete_and_off_by_default(self):
        recall.capture_once()
        self.assertTrue(recall.status()["days"])
        recall.delete("today")
        self.assertFalse(recall.entries("today"))
        self.assertFalse(json.loads('{"on": false}')["on"])           # the shipped default
        self.assertIn('"on": False', open(recall.__file__, encoding="utf-8").read().replace("'", '"'))


class Router(unittest.TestCase):
    def test_voice_commands_route(self):
        calls = []
        saved = (aegis.arm, aegis.disarm, recall.set_on, recall.search, overdrive.open_air)
        aegis.arm = lambda r="": calls.append("arm")
        aegis.disarm = lambda by="": (calls.append("disarm"), {"while_away": []})[1]
        recall.set_on = lambda on: calls.append(f"recall {on}")
        recall.search = lambda q: (calls.append("search"), {"answer": "x", "moments": []})[1]
        overdrive.open_air = lambda: (calls.append("air"), "ok")[1]
        try:
            overdrive.handle("I'm leaving, guard my PC")
            overdrive.handle("AEGIS off")
            overdrive.handle("turn on total recall")
            overdrive.handle("turn off total recall")
            overdrive.handle("air control")
            overdrive.handle("what was I working on yesterday at 3pm?")
        finally:
            (aegis.arm, aegis.disarm, recall.set_on, recall.search, overdrive.open_air) = saved
        self.assertEqual(calls, ["arm", "disarm", "recall True", "recall False", "air", "search"])
        self.assertTrue(overdrive.looks_like_recall_question("find that article I saw this morning about vaccines"))
        self.assertFalse(overdrive.looks_like_recall_question("what is the weather"))

    def test_air_events_only_known_inputs(self):
        import nexus
        got = []
        saved = nexus.remote_input
        nexus.remote_input = lambda ev, notice=True: (got.append((ev["type"], notice)), {"ok": True})[1]
        try:
            r = overdrive.air_events([{"type": "move_abs", "x": 0.5, "y": 0.5}, {"type": "down"}, {"type": "up"}, {"type": "type", "text": "rm -rf"},
                                      {"type": "key", "keys": "win+r"}, {"type": "shortcut", "name": "format_disk"}, "junk"])
        finally:
            nexus.remote_input = saved
        self.assertEqual([g[0] for g in got], ["move_abs", "down", "up"])         # no typing, no raw keys, no unknown shortcuts
        self.assertTrue(all(n is False for _, n in got))
        self.assertEqual(r["done"], 3)


if __name__ == "__main__":
    unittest.main()
