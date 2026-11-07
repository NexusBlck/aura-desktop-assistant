# Tests for the public-release pieces: .env configuration, missing-setup handling and safety.py.
import importlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

KEYS = ("LLM_API_KEY", "USER_NAME", "USER_ABOUT", "SAFE_MODE", "WAKE_WORD_ENABLED", "PHONE_REMOTE_CONTROL",
        "MIC_DEVICE_INDEX", "EXTRA_SEARCH_FOLDERS", "VOICE_LANGUAGE", "AURA_ENV_FILE")


class _EnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in KEYS}
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8")
        self.tmp.close()

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        os.remove(self.tmp.name)
        importlib.reload(config)

    def load(self, env_text):
        with open(self.tmp.name, "w", encoding="utf-8") as f:
            f.write(env_text)
        os.environ["AURA_ENV_FILE"] = self.tmp.name
        return importlib.reload(config)


class ConfigTests(_EnvCase):
    def test_safe_defaults_with_no_env_file(self):
        c = self.load("")
        self.assertEqual(c.LLM_API_KEY, "")
        self.assertTrue(c.SAFE_MODE)
        self.assertFalse(c.WAKE_WORD_ENABLED)
        self.assertFalse(c.PHONE_REMOTE_CONTROL)
        self.assertFalse(c.DEV_LOGIN_BYPASS)
        self.assertIsNone(c.MIC_DEVICE_INDEX)
        self.assertEqual(c.describe_user(), "the user")

    def test_missing_key_gives_setup_message(self):
        c = self.load("# nothing set\n")
        self.assertIn("LLM_API_KEY", c.missing_setup())
        c = self.load("LLM_API_KEY=test-not-a-real-key\n")
        self.assertEqual(c.missing_setup(), "")

    def test_env_file_values_and_types(self):
        c = self.load('LLM_API_KEY="test-key"\nUSER_NAME=Sam  # comment\nUSER_ABOUT=a student\n'
                      "SAFE_MODE=false\nWAKE_WORD_ENABLED=yes\nMIC_DEVICE_INDEX=2\n"
                      "EXTRA_SEARCH_FOLDERS=D:\\Uni; D:\\Work\nVOICE_LANGUAGE=auto\nexport PHONE_REMOTE_CONTROL=1\n")
        self.assertEqual(c.LLM_API_KEY, "test-key")
        self.assertEqual(c.USER_NAME, "Sam")
        self.assertEqual(c.describe_user(), "Sam, a student")
        self.assertFalse(c.SAFE_MODE)
        self.assertTrue(c.WAKE_WORD_ENABLED)
        self.assertTrue(c.PHONE_REMOTE_CONTROL)
        self.assertEqual(c.MIC_DEVICE_INDEX, 2)
        self.assertEqual(c.EXTRA_SEARCH_FOLDERS, ["D:\\Uni", "D:\\Work"])
        self.assertIsNone(c.VOICE_LANGUAGE)

    def test_environment_variable_overrides_env_file(self):
        os.environ["USER_NAME"] = "FromEnvironment"
        c = self.load("USER_NAME=FromFile\n")
        self.assertEqual(c.USER_NAME, "FromEnvironment")

    def test_bad_number_falls_back(self):
        c = self.load("MIC_DEVICE_INDEX=abc\n")
        self.assertIsNone(c.MIC_DEVICE_INDEX)


class SafetyTests(_EnvCase):
    def setUp(self):
        super().setUp()
        import safety
        import phone_bridge
        self.safety, self.pb = safety, phone_bridge
        self._device = phone_bridge.current_device

    def tearDown(self):
        self.pb.current_device = self._device
        super().tearDown()

    def as_device(self, device):
        self.pb.current_device = lambda: device

    def test_confirmation_needed_for_code_and_force_close(self):
        self.load("SAFE_MODE=true\n")
        for action in ("build_project", "modify_project", "close_app"):
            self.assertTrue(self.safety.needs_confirmation(action))
        self.assertFalse(self.safety.needs_confirmation("open_app"))
        self.load("SAFE_MODE=false\n")
        self.assertFalse(self.safety.needs_confirmation("build_project"))

    def test_watch_over_internet_gets_allow_list_only(self):
        self.load("SAFE_MODE=true\n")
        self.as_device({"id": "w", "watch": True})
        self.assertTrue(self.safety.from_public_internet())
        self.assertEqual(self.safety.remote_refusal("set_reminder"), "")
        self.assertEqual(self.safety.remote_refusal("chat"), "")
        for action in ("screen_action", "type_text", "build_project", "shutdown", "overdrive", "email", "screenshot"):
            self.assertTrue(self.safety.remote_refusal(action), action)
        self.assertTrue(self.safety.remote_refusal("chat", recall_question=True))

    def test_paired_phone_and_pc_are_not_restricted(self):
        self.load("SAFE_MODE=true\n")
        self.as_device({"id": "p", "watch": False})
        self.assertEqual(self.safety.remote_refusal("screen_action"), "")
        self.as_device(None)
        self.assertEqual(self.safety.remote_refusal("build_project"), "")

    def test_sensitive_feature_list(self):
        self.load("WAKE_WORD_ENABLED=true\nPHONE_REMOTE_CONTROL=true\nSAFE_MODE=false\n")
        text = " ".join(self.safety.active_sensitive_features())
        self.assertIn("wake word", text)
        self.assertIn("LINK", text)
        self.assertIn("SAFE_MODE is OFF", text)


class WiringTests(unittest.TestCase):
    """The safety hooks must stay connected in main.py."""

    def test_main_uses_safety(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8").read()
        self.assertIn("safety.remote_refusal(", src)
        self.assertIn("safety.needs_confirmation(action)", src)
        self.assertIn("config.missing_setup()", src)
        self.assertIn("if not WAKE_WORD_ENABLED:", src)
        self.assertIn("isinstance(e, AuthenticationError)", src)


class RejectedKeyTests(unittest.TestCase):
    """A wrong key must surface as openai.AuthenticationError (main.py turns it into a clear spoken message)."""

    def test_auth_error_propagates_from_ask_llm(self):
        import ai
        from openai import AuthenticationError

        def fake_create(*a, **k):
            import httpx
            raise AuthenticationError("Invalid API Key", response=httpx.Response(401, request=httpx.Request("POST", "https://x")),
                                      body=None)

        orig = ai._create
        ai._create = fake_create
        try:
            with self.assertRaises(AuthenticationError):
                ai.ask_llm([{"role": "user", "content": "hi"}], models=["test-model"])
        finally:
            ai._create = orig


if __name__ == "__main__":
    unittest.main()
