# Tests for AURA Mobile's bridge: pairing, device tokens, command origin, file-link scoping.
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import phone_bridge as pb


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._file = pb.DEVICES_FILE
        pb.DEVICES_FILE = os.path.join(self.tmp, "devices.json")

    def tearDown(self):
        pb.DEVICES_FILE = self._file

    def test_right_code_pairs_once(self):
        code = pb.new_pairing_code(show=False)
        token, device = pb._pair(code, "Test phone")
        self.assertEqual(pb._device_for(token)["id"], device["id"])
        self.assertIsNone(pb._pair(code, "again"))            # a code works only once

    def test_token_is_stored_hashed(self):
        code = pb.new_pairing_code(show=False)
        token, _ = pb._pair(code, "Test phone")
        with open(pb.DEVICES_FILE, encoding="utf-8") as f:
            self.assertNotIn(token, f.read())

    def test_wrong_codes_lock_out(self):
        code = pb.new_pairing_code(show=False)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertIsNone(pb._pair(wrong, "x"))
        self.assertIsNone(pb._pair(code, "x"))               # 5 misses -> the code is dead

    def test_expired_code(self):
        code = pb.new_pairing_code(show=False)
        pb._pairing["expires"] = 0
        self.assertIsNone(pb._pair(code, "x"))

    def test_revoke(self):
        token, device = pb._pair(pb.new_pairing_code(show=False), "x")
        self.assertTrue(pb._revoke(device["id"]))
        self.assertIsNone(pb._device_for(token))
        self.assertIsNone(pb._device_for(""))
        self.assertIsNone(pb._device_for("made-up"))


class OriginTests(unittest.TestCase):
    def test_phone_text_is_a_str(self):
        t = pb.PhoneText("Hello")
        self.assertIsInstance(t, str)
        self.assertEqual(t.lower(), "hello")

    def test_origin_only_for_phone_commands_on_that_thread(self):
        pb.begin_command("typed on the PC")
        self.assertFalse(pb.phone_origin())
        pb.begin_command(pb.PhoneText("from the phone"))
        self.assertTrue(pb.phone_origin())
        seen = []
        th = threading.Thread(target=lambda: seen.append(pb.phone_origin()))
        th.start(); th.join()
        self.assertEqual(seen, [False])
        pb.end_command()
        self.assertFalse(pb.phone_origin())


class FileLinkTests(unittest.TestCase):
    def test_no_escaping_the_folders(self):
        self.assertIsNone(pb._resolve("briefings", "../../config.py"))
        self.assertIsNone(pb._resolve("aura", "config.py"))          # only listed AURA files
        self.assertIsNone(pb._resolve("nonsense", "x"))
        self.assertIsNone(pb._ticket("aura", "aura_phone_devices.json"))

    def test_single_file_link_unlocks_only_that_file(self):
        folder = pb._roots()["uploads"]
        os.makedirs(folder, exist_ok=True)
        a, b = os.path.join(folder, "_t_a.txt"), os.path.join(folder, "_t_b.txt")
        for p in (a, b):
            with open(p, "w") as f:
                f.write("x")
        try:
            link = pb._ticket("uploads", "_t_a.txt")
            t = pb._tickets[link.split("/")[2]]
            self.assertEqual(t["only"], "_t_a.txt")
        finally:
            os.remove(a); os.remove(b)



class RoutingTests(unittest.TestCase):
    def test_on_my_pc_is_detected_and_removed(self):
        import phone_actions as pa
        self.assertEqual(pa.split_target("open spotify on my laptop"), ("open spotify", True))
        self.assertEqual(pa.split_target("take a screenshot on my PC please")[1], True)
        self.assertEqual(pa.split_target("open spotify"), ("open spotify", False))
        self.assertEqual(pa.split_target("open youtube on my phone"), ("open youtube", False))

    def test_begin_command_sets_target(self):
        t = pb.PhoneText("open whatsapp on my computer")
        t.device = "d1"
        out = pb.begin_command(t)
        self.assertEqual(str(out), "open whatsapp")
        self.assertIsInstance(out, pb.PhoneText)
        self.assertEqual(out.device, "d1")
        self.assertFalse(pb.phone_target())
        pb.begin_command(pb.PhoneText("open whatsapp"))
        self.assertTrue(pb.phone_target())
        pb.end_command()
        pb.begin_command("typed on the PC")
        self.assertFalse(pb.phone_target())
        pb.end_command()

    def test_urls_become_phone_urls(self):
        import phone_actions as pa
        self.assertEqual(pa._phone_url("spotify:track:abc123"), "https://open.spotify.com/track/abc123")
        self.assertEqual(pa._phone_url("https://example.com/x"), "https://example.com/x")

    def test_pc_only_actions_are_offered_not_done(self):
        import phone_actions as pa
        sent = []
        orig = pb.emit
        pb.emit = lambda kind, **d: sent.append((kind, d))
        try:
            reply = pa.handle({"action": "build_project"}, "build me a todo app")
        finally:
            pb.emit = orig
        self.assertIn("on my PC", reply)
        self.assertEqual(sent[0][1]["action"], "pc_offer")


if __name__ == "__main__":
    unittest.main()
