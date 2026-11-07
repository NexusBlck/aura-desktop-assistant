# Tests for nexus.py (AURA Mobile's NEXUS): voice triggers, protocols, places maths, the remote-control switch.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nexus


class TriggerTests(unittest.TestCase):
    def test_protocol_phrases(self):
        self.assertEqual(nexus.match_protocol("run study protocol")["id"], "study")
        self.assertEqual(nexus.match_protocol("AURA, engage the night protocol.")["id"], "night")
        self.assertIsNone(nexus.match_protocol("what is a protocol"))
        self.assertIsNone(nexus.match_protocol("run the banana protocol"))

    def test_phone_tools(self):
        cases = {"start live mode": "nexus-live", "open radar": "nexus-radar", "check my heart rate": "nexus-pulse",
                 "record this lecture": "nexus-echo", "open the interpreter": "nexus-babel", "open nexus": "nexus"}
        for text, tool in cases.items():
            self.assertEqual(nexus.phone_tool(text), tool, text)
        for text in ("open youtube", "what is my heart rate like in general", "run study protocol"):
            self.assertIsNone(nexus.phone_tool(text), text)


class ProtocolStoreTests(unittest.TestCase):
    def setUp(self):
        self._file = nexus.PROTOCOLS_FILE
        nexus.PROTOCOLS_FILE = os.path.join(tempfile.mkdtemp(), "protocols.json")

    def tearDown(self):
        nexus.PROTOCOLS_FILE = self._file

    def test_save_edit_delete(self):
        self.assertIn("error", nexus.protocol_save({"name": "Gym", "steps": ["", "  "]}))
        r = nexus.protocol_save({"name": "Gym", "steps": ["play workout music", "", "start a 45 minute focus session"]})
        self.assertEqual(r["protocol"]["steps"], ["play workout music", "start a 45 minute focus session"])
        self.assertEqual(nexus.match_protocol("run gym protocol")["id"], "gym")
        nexus.protocol_save({"id": "gym", "name": "Gym", "steps": ["only step"]})
        self.assertEqual(next(p for p in nexus.protocols_list() if p["id"] == "gym")["steps"], ["only step"])
        self.assertFalse(any(p["id"] == "gym" for p in nexus.protocol_delete("gym")["items"]))


class GeoTests(unittest.TestCase):
    def test_distance_and_bearing(self):
        d, b = nexus._dist_bearing(24.86, 67.0, 24.87, 67.0)          # ~1.1 km due north
        self.assertAlmostEqual(d, 1112, delta=5)
        self.assertLess(min(b, 360 - b), 1)


class RemoteSwitchTests(unittest.TestCase):
    def test_remote_can_be_switched_off(self):
        import config
        import phone_bridge
        old = getattr(config, "PHONE_REMOTE_CONTROL", None)
        config.PHONE_REMOTE_CONTROL = False
        try:
            self.assertIn("error", phone_bridge._nexus("GET", "screen", {}, {}, {"id": "x"}))
            self.assertIn("error", phone_bridge._nexus("POST", "input", {}, {"type": "click"}, {"id": "x"}))
        finally:
            if old is None:
                del config.PHONE_REMOTE_CONTROL
            else:
                config.PHONE_REMOTE_CONTROL = old


if __name__ == "__main__":
    unittest.main()
