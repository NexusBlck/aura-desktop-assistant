# Tests for phone_sync.py: the phone brain's changes are applied once (retries never double anything),
# unknown changes are reported, and the kit has what the phone needs.
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import phone_sync


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._file, self._apply = phone_sync.SYNC_FILE, phone_sync._apply_one
        phone_sync.SYNC_FILE = os.path.join(tempfile.mkdtemp(), "sync.json")
        self.applied = []
        phone_sync._apply_one = lambda kind, d, device: self.applied.append((kind, d.get("text")))

    def tearDown(self):
        phone_sync.SYNC_FILE, phone_sync._apply_one = self._file, self._apply

    def test_each_change_applied_once(self):
        ops = [{"id": "a", "type": "task_add", "data": {"text": "x"}}, {"id": "b", "type": "chat", "data": {"text": "hi"}}]
        self.assertEqual(phone_sync.apply(ops, {"id": "d"})["done"], ["a", "b"])
        self.assertEqual(phone_sync.apply(ops, {"id": "d"})["done"], ["a", "b"])      # a retry reports them done...
        self.assertEqual(len(self.applied), 2)                                         # ...without applying them again

    def test_failures_are_reported_and_retried_later(self):
        phone_sync._apply_one = self._apply                     # the real one: an unknown type raises
        r = phone_sync.apply([{"id": "z", "type": "nonsense", "data": {}}, {"no": "id"}], {"id": "d"})
        self.assertEqual(r["done"], [])
        self.assertEqual(r["failed"][0]["id"], "z")


class KitTests(unittest.TestCase):
    def test_kit_has_what_the_phone_needs(self):
        k = phone_sync.kit()
        for key in ("keys", "groq_base", "models", "memory", "notes", "protocols"):
            self.assertIn(key, k)
        self.assertTrue(k["models"]["groq"])
        self.assertIn("facts", k["memory"])


if __name__ == "__main__":
    unittest.main()
