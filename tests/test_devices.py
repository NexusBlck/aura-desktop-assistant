# Several devices (iPhone + iPad): AURA's spoken answers and "open this" actions are tagged with the device that
# asked, so only that one speaks / opens it. PC commands and status stay untagged (everyone sees them).
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import phone_bridge as pb


def last_event():
    return json.loads(pb._events[-1][1])


class DeviceTargetTests(unittest.TestCase):
    def tearDown(self):
        pb.end_command()

    def test_reply_and_actions_go_to_the_device_that_asked(self):
        ask = pb.PhoneText("what's the time")
        ask.device = "ipad-123"
        pb.begin_command(ask)
        pb.emit("log", role="aura", text="It's noon.", for_phone=True)
        self.assertEqual(last_event()["device"], "ipad-123")
        pb.emit("do", action="open", url="https://youtube.com")
        self.assertEqual(last_event()["device"], "ipad-123")
        pb.emit("status", thinking=True)
        self.assertNotIn("device", last_event())

    def test_helper_threads_keep_the_device(self):
        ask = pb.PhoneText("research x")
        ask.device = "iphone-9"
        pb.begin_command(ask)
        seen = {}
        pb.carry(lambda: seen.update(device=pb._local.device))()
        self.assertEqual(seen["device"], "iphone-9")

    def test_pc_commands_are_untagged(self):
        pb.begin_command("typed on the PC")
        pb.emit("log", role="aura", text="Done.")
        self.assertNotIn("device", last_event())


class WatchTests(unittest.TestCase):
    def test_watch_gets_auras_answer_in_the_same_request(self):
        import queue
        import threading
        import time
        old_q = pb._queue
        pb._queue = queue.Queue()

        def aura():
            cmd = pb.begin_command(pb._queue.get(timeout=5))
            self.assertEqual(pb._local.target, "pc")                 # the watch acts on the PC
            pb.emit("status", thinking=True)
            pb.emit("log", role="aura", text="Let me think that through properly.", for_phone=True)
            time.sleep(0.4)
            pb.emit("log", role="aura", text="You have two tasks.", for_phone=True)
            pb.emit("status", thinking=False)
            pb.end_command()
        threading.Thread(target=aura, daemon=True).start()
        try:
            r = pb.watch_ask("what are my tasks", {"id": "watch-1"}, timeout=8)
        finally:
            pb._queue = old_q
        self.assertEqual(r["reply"], "You have two tasks.")           # the filler line is dropped


if __name__ == "__main__":
    unittest.main()
