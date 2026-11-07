# tests/test_core.py
# Run from the AURA folder with:  python -m unittest discover tests
# All LLM calls are mocked - these run offline, instantly, and cost nothing.
import os
import sys
import json
import datetime
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai
import intent
import event_parser
import life_data


class ExtractJsonTests(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(ai.extract_json('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        self.assertEqual(ai.extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_text_around_json(self):
        self.assertEqual(ai.extract_json('Sure! Here you go: {"a": [1, 2]} hope that helps'), {"a": [1, 2]})

    def test_array(self):
        self.assertEqual(ai.extract_json("[0, 3]"), [0, 3])

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            ai.extract_json(None)

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            ai.extract_json("no json here")

    def test_ask_json_returns_default_on_failure(self):
        with mock.patch("ai.ask_llm", side_effect=RuntimeError("boom")):
            self.assertEqual(ai.ask_json([], default=[]), [])


class IntentTests(unittest.TestCase):
    def test_write_note_with_other_app_becomes_open_and_type(self):
        fake = {"action": "write_note", "target": "hello"}
        with mock.patch("intent.ask_json", return_value=fake):
            result = intent.classify_intent("open word and write hello")
        self.assertEqual(result["action"], "open_and_type")
        self.assertEqual(result["app"], "word")
        self.assertFalse(result["submit"])

    def test_write_note_with_notepad_stays(self):
        fake = {"action": "write_note", "target": "buy milk"}
        with mock.patch("intent.ask_json", return_value=fake):
            result = intent.classify_intent("open notepad and write buy milk")
        self.assertEqual(result["action"], "write_note")

    def test_follow_up_types_into_open_window(self):
        # Word is already open; "write AURA on it" must NOT open a new window.
        fake = {"action": "open_and_type", "app": "word", "target": "AURA"}
        with mock.patch("intent.ask_json", return_value=fake):
            result = intent.classify_intent("write AURA on it", open_target="Word (app), opened just now")
        self.assertEqual(result["action"], "type_text")

    def test_explicit_open_still_opens_even_with_something_open(self):
        fake = {"action": "open_and_type", "app": "notepad", "target": "hi"}
        with mock.patch("intent.ask_json", return_value=fake):
            result = intent.classify_intent("open notepad and write hi", open_target="Word (app), opened just now")
        self.assertEqual(result["action"], "open_and_type")

    def test_open_target_reaches_the_prompt(self):
        with mock.patch("intent.ask_json", return_value={"action": "chat", "reply": "ok"}) as m:
            intent.classify_intent("save it", open_target="Word (app), opened just now")
        self.assertIn("Currently open", m.call_args[0][0][0]["content"])

    def test_bad_reply_falls_back_to_chat(self):
        with mock.patch("intent.ask_json", return_value=None):
            result = intent.classify_intent("hey")
        self.assertEqual(result["action"], "chat")
        self.assertEqual(result["target"], "hey")

    def test_defaults_filled_in(self):
        with mock.patch("intent.ask_json", return_value={"action": "open_app", "target": "spotify"}):
            result = intent.classify_intent("open spotify")
        for key in ("reply", "memorable", "app", "submit", "search_mode"):
            self.assertIn(key, result)

    def test_known_facts_reach_the_prompt(self):
        with mock.patch("intent.ask_json", return_value={"action": "chat", "reply": "hi"}) as m:
            intent.classify_intent("who am i", known_facts=["my name is Sam"])
        system_prompt = m.call_args[0][0][0]["content"]
        self.assertIn("my name is Sam", system_prompt)


class EventParserTests(unittest.TestCase):
    def test_timed_event(self):
        fake = {"summary": "BJJ", "all_day": False, "date": "2026-10-01", "start_time": "19:00", "end_time": None}
        with mock.patch("event_parser.ask_json", return_value=fake):
            parsed = event_parser.parse_event("BJJ at 7pm on Oct 1")
        self.assertEqual(parsed["summary"], "BJJ")
        self.assertFalse(parsed["all_day"])
        self.assertEqual((parsed["start"].hour, parsed["start"].minute), (19, 0))
        self.assertIsNone(parsed["end"])

    def test_all_day_event(self):
        fake = {"summary": "Deadline", "all_day": True, "date": "2026-10-03", "start_time": None, "end_time": None}
        with mock.patch("event_parser.ask_json", return_value=fake):
            parsed = event_parser.parse_event("deadline in 2 days")
        self.assertTrue(parsed["all_day"])
        self.assertEqual(parsed["start"].date(), datetime.date(2026, 10, 3))

    def test_bad_reply_returns_none(self):
        with mock.patch("event_parser.ask_json", return_value=None):
            self.assertIsNone(event_parser.parse_event("???"))

    def test_malformed_date_returns_none(self):
        with mock.patch("event_parser.ask_json", return_value={"date": "tomorrow", "all_day": True}):
            self.assertIsNone(event_parser.parse_event("tomorrow"))


class LifeDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        os.remove(self.tmp.name)
        self.patches = [
            mock.patch("life_data.LIFE_DATA_FILE", self.tmp.name),
            mock.patch("tasks.load_tasks", return_value=[]),
            mock.patch("paper_library.load_library", return_value=[]),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def test_parse_ts_naive(self):
        dt = life_data._parse_ts("2026-09-05T20:00:00")
        self.assertIsNone(dt.tzinfo)

    def test_parse_ts_aware_and_z_become_naive(self):
        self.assertIsNone(life_data._parse_ts("2026-09-05T20:00:00+05:00").tzinfo)
        self.assertIsNone(life_data._parse_ts("2026-09-05T15:00:00Z").tzinfo)

    def test_mixed_timestamps_dont_crash_summaries(self):
        # Before the fix, mixing these raised
        # "can't compare offset-naive and offset-aware datetimes".
        now = datetime.datetime.now()
        with open(self.tmp.name, "w", encoding="utf-8") as f:
            json.dump({"sessions": [
                {"type": "judo", "timestamp": now.isoformat(), "rating": 7},
                {"type": "bjj", "timestamp": now.astimezone().isoformat(), "rating": None},
                {"type": "gym", "timestamp": (now - datetime.timedelta(hours=1)).astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z"), "rating": 5},
            ], "journal": []}, f)

        summary = life_data.get_weekly_summary()
        self.assertEqual(summary["total_sessions"], 3)
        life_data.get_today_summary()
        trend = life_data.get_trend_data()
        self.assertEqual(sum(trend["session_type_totals"].values()), 3)

    def test_log_session_normalizes_timestamp(self):
        life_data.log_session("BJJ", timestamp="2026-09-10T20:00:00+05:00", rating=8)
        saved = life_data.load_data()["sessions"][0]
        self.assertEqual(saved["type"], "bjj")
        self.assertIsNone(datetime.datetime.fromisoformat(saved["timestamp"]).tzinfo)

    def test_session_type_summary(self):
        life_data.log_session("judo", rating=6)
        life_data.log_session("judo", rating=8)
        life_data.log_session("judo")
        summary = life_data.get_session_type_summary("Judo")
        self.assertEqual(summary, {"count": 3, "avg_rating": 7.0})


class FileSearchTests(unittest.TestCase):
    INDEX = {
        "built_at": 0,
        "files": {
            r"C:\Docs\Research\Systematic Reviews\PRISMA 2020.pdf": {"name": "PRISMA 2020.pdf", "mtime": 2, "size": 1, "snippet": "flow diagram"},
            r"C:\Docs\Research\Systematic Reviews\Notes.docx": {"name": "Notes.docx", "mtime": 1, "size": 1, "snippet": "inclusion criteria"},
            r"C:\Docs\Pharma\Antibiotics.docx": {"name": "Antibiotics.docx", "mtime": 1, "size": 1, "snippet": "dosage of amoxicillin"},
            r"C:\Docs\Games\Roblox codes.txt": {"name": "Roblox codes.txt", "mtime": 1, "size": 1, "snippet": "free items"},
        },
        "folders": {
            r"C:\Docs\Research\Systematic Reviews": "Systematic Reviews",
            r"C:\Docs\Pharma": "Pharma",
            r"C:\Docs\Games": "Games",
        },
    }

    def setUp(self):
        import relevance
        self.relevance = relevance
        self.p = mock.patch("relevance.get_index", return_value=self.INDEX)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_specific_file_matched_by_name_without_ai(self):
        with mock.patch("relevance.ask_json") as ai_call:
            result = self.relevance.find_relevant("PRISMA 2020", mode="specific")
        self.assertEqual(result["files"], [r"C:\Docs\Research\Systematic Reviews\PRISMA 2020.pdf"])
        ai_call.assert_not_called()

    def test_topic_search_uses_expanded_words_and_matching_folder(self):
        def fake_ai(messages, **kwargs):
            if "keywords" in messages[0]["content"]:
                return {"keywords": ["prisma", "inclusion criteria"]}
            listing = messages[-1]["content"]
            return [line[6:] for line in listing.splitlines() if line.startswith("PATH: ") and "Systematic Reviews" in line]

        with mock.patch("relevance.ask_json", side_effect=fake_ai):
            result = self.relevance.find_relevant("systematic reviews")
        self.assertEqual(result["folders"], [r"C:\Docs\Research\Systematic Reviews"])
        self.assertEqual(set(result["files"]), {
            r"C:\Docs\Research\Systematic Reviews\PRISMA 2020.pdf",
            r"C:\Docs\Research\Systematic Reviews\Notes.docx",
        })

    def test_ai_rejections_are_respected(self):
        def fake_ai(messages, **kwargs):
            if "keywords" in messages[0]["content"]:
                return {"keywords": ["antibiotic", "dosage", "roblox"]}
            return []  # the AI says none of the candidates are really about it

        with mock.patch("relevance.ask_json", side_effect=fake_ai):
            result = self.relevance.find_relevant("medicine")
        self.assertEqual(result["files"], [])

    def test_filler_words_ignored(self):
        self.assertEqual(self.relevance._words("bring up everything we have on medicine"), ["medicine"])


class WebBriefingTests(unittest.TestCase):
    def setUp(self):
        import web_briefing
        self.wb = web_briefing

    def test_pick_accepts_any_number_format(self):
        items = ["a", "b", "c"]
        self.assertEqual(self.wb._pick([3, "1", "I2", "[V2]", 99, "x"], items, 10), ["c", "a", "b"])

    def test_citations_become_links_and_text_is_escaped(self):
        articles = [{"url": "https://example.com/story"}]
        out = self.wb._cite_links("Police said <b>this</b> [A1] and [A7]", articles)
        self.assertIn('href="https://example.com/story"', out)
        self.assertIn("&lt;b&gt;", out)       # page can't be broken by text from a website
        self.assertNotIn("[A7]", out)          # citation to a non-existent source is dropped

    def test_youtube_id(self):
        self.assertEqual(self.wb._youtube_id("https://www.youtube.com/watch?v=hLYn4LOuaUg"), "hLYn4LOuaUg")
        self.assertIsNone(self.wb._youtube_id("https://example.com"))

    def test_choose_falls_back_to_article_photos_and_videos(self):
        articles = [{"title": "T", "url": "https://n.com/1", "text": "x", "source": "n.com", "date": ""}]
        other = {"url": "https://x.com/o.jpg", "title": "other", "page": ""}
        own = {"url": "https://n.com/p.jpg", "title": "photo", "page": "https://n.com/1"}
        videos = [{"id": "hLYn4LOuaUg", "title": "clip", "duration": "1:00", "age": "1d ago"}]
        chosen, candidates, vids = self.wb._choose({"images": [], "videos": []}, articles, [other, own], videos)
        self.assertEqual(candidates[0], own)   # the article's own photo is tried first
        self.assertEqual(vids, videos)

    def test_exact_names_found_in_request(self):
        self.assertEqual(self.wb._exact_names("bring up everything about this gym called RFC"), ["RFC"])
        self.assertIn("Gold Star Karate", self.wb._exact_names('everything about "Gold Star Karate"'))

    def test_look_alike_is_filtered_out(self):
        names = self.wb._distinctive_names(["RFC"], "Riverside Fitness Club (RFC), Leeds")
        self.assertEqual(names, ["RFC", "Riverside"])
        items = [
            {"title": "Boxing classes at R.Fitness Studio", "text": "Headingley"},
            {"title": "At RFC we believe boxing is a way of life", "text": ""},
            {"title": "Official website - about", "text": "...", "deep": True},  # always kept
        ]
        kept = self.wb._keep_named(items, names)
        self.assertEqual([i["title"][:9] for i in kept], ["At RFC we", "Official "])

    def test_every_cited_source_is_listed(self):
        articles = [{"title": f"T{i}", "url": f"https://n.com/{i}", "text": "", "source": "n.com", "date": ""} for i in range(1, 8)]
        brief = {"articles": [1, 2], "summary": ["x [A6]"], "key_points": ["y [A7] and [A2]"], "images": [], "videos": []}
        chosen, _, _ = self.wb._choose(brief, articles, [], [])
        self.assertEqual([a["url"] for a in chosen], ["https://n.com/1", "https://n.com/2", "https://n.com/6", "https://n.com/7"])

    def test_page_uses_downloaded_copy_and_playable_videos(self):
        articles = [{"title": "T", "url": "https://n.com/1", "text": "x", "source": "n.com", "date": ""}]
        pictures = [{"url": "https://n.com/p.jpg", "title": "photo", "page": "https://n.com/1",
                     "local": "brief_pictures/picture-01.jpg"}]
        videos = [{"id": "hLYn4LOuaUg", "title": "clip", "duration": "1:00", "age": "1d ago"}]
        page = self.wb._build_page("T", "req", {"summary": ["s [A1]"]}, articles, articles, pictures, videos)
        self.assertIn('src="brief_pictures/picture-01.jpg"', page)
        self.assertIn("youtube-nocookie.com/embed/hLYn4LOuaUg", page)
        self.assertIn('referrerpolicy="strict-origin-when-cross-origin"', page)

    def test_download_rejects_logos_and_keeps_real_photos(self):
        import io, tempfile
        from PIL import Image

        def fake_image(size):
            buf = io.BytesIO()
            Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
            resp = mock.Mock(status_code=200)
            resp.raw.read.return_value = buf.getvalue()
            return resp

        folder = tempfile.mkdtemp()
        with mock.patch("requests.get", side_effect=[fake_image((64, 64)), fake_image((800, 500))]):
            logo = self.wb._download_picture({"url": "https://n.com/logo.png"}, folder, 1)
            photo = self.wb._download_picture({"url": "https://n.com/photo.png"}, folder, 2)
        self.assertIsNone(logo)
        self.assertEqual(photo[0], "picture-02.jpg")
        self.assertTrue(os.path.exists(os.path.join(folder, "picture-02.jpg")))


class ProductivityTests(unittest.TestCase):
    def test_relative_reminder_parsed_without_ai(self):
        import reminders
        with mock.patch("reminders.ask_json") as ai_call:
            text, due = reminders.parse("remind me in 20 minutes to call Ali")
        ai_call.assert_not_called()
        self.assertEqual(text, "Call Ali")
        mins = (due - reminders._now()).total_seconds() / 60
        self.assertTrue(19.5 < mins <= 20)

    def test_reminder_without_time_asks_when(self):
        import reminders
        with mock.patch("reminders.ask_json", return_value={"text": "Buy milk", "due": None}):
            self.assertIn("When should I remind you", reminders.add_from_text("remind me to buy milk"))

    def test_reminders_fire_and_are_saved(self):
        import reminders, datetime
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json"); tmp.close(); os.remove(tmp.name)
        with mock.patch("reminders.REMINDERS_FILE", tmp.name):
            reminders.add("Call Ali", reminders._now() - datetime.timedelta(seconds=1))
            reminders.add("Later thing", reminders._now() + datetime.timedelta(hours=1))
            self.assertEqual([r["text"] for r in reminders.pending()], ["Call Ali", "Later thing"])
            fired = []
            with mock.patch("reminders.time.sleep", side_effect=[None, SystemExit]):
                with mock.patch("reminders.threading.Thread") as T:
                    reminders.start_checker(fired.append)
                    loop = T.call_args.kwargs["target"]
                    with self.assertRaises(SystemExit):
                        loop()
            self.assertEqual([r["text"] for r in fired], ["Call Ali"])
            self.assertEqual([r["text"] for r in reminders.pending()], ["Later thing"])
        os.remove(tmp.name)

    def test_focus_lengths_understood(self):
        import focus
        cases = {"start a 50 minute focus session on anatomy": (50, "Anatomy"), "focus for an hour": (60, "Focus"),
                 "focus for half an hour": (30, "Focus"), "focus for an hour and a half": (90, "Focus"),
                 "focus 1 hour 15 minutes": (75, "Focus"), "pomodoro": (25, "Focus")}
        for request, (minutes, label) in cases.items():
            focus.handle_request(request)
            st = focus.status()
            self.assertEqual((st["total"] // 60, st["label"]), (minutes, label), request)
        self.assertEqual(focus.handle_request("pause the timer"), "Paused.")
        self.assertEqual(focus.status()["mode"], "paused")
        focus.handle_request("resume")
        self.assertEqual(focus.status()["mode"], "focus")
        focus.handle_request("stop focus")
        self.assertEqual(focus.status()["mode"], "idle")

    def test_new_intents_routed(self):
        with mock.patch("intent.ask_json", return_value={"action": "set_reminder", "target": "remind me in 5 min to stretch"}):
            self.assertEqual(intent.classify_intent("remind me in 5 min to stretch")["action"], "set_reminder")


class VisionModeTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        import camera
        self.camera = camera
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[100:300, 200:500] = (255, 255, 255)  # a white "object"
        self.p = mock.patch("camera.latest_frame", return_value=(frame, 1))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_identify_crops_the_clicked_area_and_returns_info(self):
        import object_vision
        reply = '{"name": "Wireless mouse", "category": "Computer peripheral", "confidence": "high", ' \
                '"summary": "A mouse.", "details": [{"label": "Colour", "value": "White"}, "junk"], "fact": "Fact."}'
        with mock.patch("object_vision._ask_vision", return_value=reply) as ask:
            info = object_vision.identify({"x": 0.15, "y": 0.14, "w": 0.24, "h": 0.28}, hint="mouse")
        self.assertEqual(info["name"], "Wireless mouse")
        self.assertEqual(info["details"], [{"label": "Colour", "value": "White"}])  # junk dropped
        prompt = ask.call_args[0][0][0]["text"]
        self.assertIn('"mouse"', prompt)  # the fast detector's guess is passed along

    def test_identify_handles_bad_input_and_camera_off(self):
        import object_vision
        self.assertIn("error", object_vision.identify({"x": "?"}))
        self.assertIn("error", object_vision.identify({"x": 0.5, "y": 0.5, "w": 0.001, "h": 0.001}))
        with mock.patch("camera.latest_frame", return_value=(None, 0)):
            self.assertEqual(object_vision.identify({"x": 0, "y": 0, "w": 1, "h": 1})["error"], "The camera isn't on.")

    def test_deep_scan_converts_to_fractions(self):
        import object_vision
        with mock.patch("object_vision._ask_vision", return_value='{"objects": [{"label": "Mouse", "x": 256, "y": 144, "w": 128, "h": 72}, {"label": "bad"}]}'):
            res = object_vision.deep_scan()
        self.assertEqual(len(res["objects"]), 1)
        o = res["objects"][0]
        self.assertAlmostEqual(o["x"], 0.25, places=2)
        self.assertAlmostEqual(o["w"], 0.125, places=2)

    def test_camera_intents_routed(self):
        with mock.patch("intent.ask_json", return_value={"action": "open_camera", "target": ""}):
            self.assertEqual(intent.classify_intent("open the camera")["action"], "open_camera")


class CdcWonderTests(unittest.TestCase):
    XML = """<page><measure-selections><measure code="D157.M1"/><measure code="D157.M2"/><measure code="D157.M3"/>
<measure code="D157.M32"/><measure code="D157.M33"/><measure code="D157.M4"/><measure code="D157.M41"/><measure code="D157.M42"/><measure code="D157.M43"/></measure-selections>
<data-table>
<r><c l="Female" r="2"/><c l="2021"/><c v="208,550"/><c v="167,509,003"/><c v="124.5" a="c"><l v="(124.0 - 125.0)"/></c><c v="90.3" a="c"><l v="(89.9 - 90.7)"/></c><c v="0.2"/></r>
<r><c l="2022 "/><c v="12"/><c v="1,000,000"/><c v="Unreliable"/><c v="Unreliable"/><c v="Unreliable"/></r>
<r><c l="Male"/><c l="2021"/><c v="Suppressed"/><c v="900,000"/><c v="Suppressed"/><c v="Suppressed"/><c v="Suppressed"/></r>
</data-table></page>"""

    def setUp(self):
        import cdc_wonder, cdc_excel
        self.W, self.X = cdc_wonder, cdc_excel

    def test_icd_codes_expand_ranges_and_catch_typos(self):
        codes, errors = self.W.parse_icd_codes("I50, I20-I22; m15.0 Z9 I25-I20")
        self.assertEqual(codes, ["I50", "I20", "I21", "I22", "M15.0"])
        self.assertEqual(len(errors), 2)

    def test_response_parsed_by_column_list_with_spans_and_flags(self):
        rows = self.W.parse_response(self.XML, "D157", grouped=True)
        self.assertEqual([(r["group"], r["year"]) for r in rows], [("Female", 2021), ("Female", 2022), ("Male", 2021)])
        f = rows[0]
        self.assertEqual((f["deaths"], f["crude_lo"], f["aar"], f["aar_lo"], f["aar_hi"], f["aar_se"]), (208550, 124.0, 90.3, 89.9, 90.7, 0.2))
        self.assertIsNone(rows[1]["aar"])
        self.assertIn("M4:unreliable", rows[1]["flags"])
        self.assertEqual(rows[2]["deaths"], "Suppressed")

    def test_error_message_surfaced(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.W.parse_response("<page><message>Age Adjusted Rates cannot be produced</message></page>", "D77", False)
        self.assertIn("Age Adjusted Rates", str(ctx.exception))

    def test_request_contains_codes_ages_consent_and_no_location(self):
        xml = self.W.build_request("D77", "V7", ["I50"], ["65-74", "75-84"], "multiple", True, and_codes=["M17"])
        for piece in ("<name>B_1</name><value>D77.V7</value>", "<name>B_2</name><value>D77.V1-level1</value>",
                      "<name>V_D77.V13</name><value>I50</value>", "<name>V_D77.V13_AND</name><value>M17</value>",
                      "<value>65-74</value><value>75-84</value>", "<name>accept_datause_restrictions</name><value>true</value>",
                      "<name>O_aar</name><value>aar_std</value>"):
            self.assertIn(piece, xml)
        self.assertNotIn("D77.V9-level1</value></parameter><parameter><name>B_2", xml)  # never grouped by location
        age = self.W.build_request("D157", "V5", ["I50"], [], "underlying", False)
        self.assertIn("<name>O_aar</name><value>aar_none</value>", age)  # CDC can't age-adjust when grouped by age
        self.assertIn("<name>F_D157.V2</name><value>I50</value>", age)

    def test_a_and_b_queries(self):
        both = self.W.build_request("D77", None, ["I50"], [], "multiple", True, and_codes=["M15", "M16"])
        self.assertIn("<name>V_D77.V13</name><value>I50</value>", both)
        self.assertIn("<name>V_D77.V13_AND</name><value>M15</value><value>M16</value>", both)
        # died FROM A (underlying) WITH B (anywhere)
        from_with = self.W.build_request("D77", None, ["I50"], [], "underlying", True, and_codes=["M15"])
        self.assertIn("<name>F_D77.V2</name><value>I50</value>", from_with)
        self.assertIn("<name>V_D77.V13</name><value>M15</value>", from_with)
        self.assertIn("<name>V_D77.V13_AND</name><value></value>", from_with)  # no second MCD box

    def test_extractor_builds_one_combined_condition(self):
        import json as _json, cdc_window
        with mock.patch("cdc_wonder.has_consent", return_value=True), mock.patch("cdc_wonder.Job") as J, \
             mock.patch("cdc_window._start_watcher"), mock.patch("cdc_window._status", return_value={}):
            J.return_value.state = "ready"
            res = _json.loads(cdc_window.CdcAPI().start(_json.dumps(
                {"a": {"name": "Heart failure", "codes": "I50"}, "b": {"name": "Arthrosis", "codes": "M15-M17"}, "ages": ["85+"], "cause": "multiple"})))
        self.assertTrue(res.get("ok"), res)
        cond = J.call_args[0][0]["conditions"]
        self.assertEqual(cond, [{"name": "Heart failure + Arthrosis", "codes": ["I50"], "label_a": "Heart failure",
                                 "and_codes": ["M15", "M16", "M17"], "label_b": "Arthrosis"}])
        cdc_window._job = None

    def test_merge_keeps_one_row_per_year(self):
        d77 = [{"group": "All", "year": y, "dataset": "D77"} for y in range(1999, 2021)]
        d157 = [{"group": "All", "year": y, "dataset": "D157"} for y in range(2018, 2025)]
        merged = self.X.merge_years(d77, d157)
        self.assertEqual([r["year"] for r in merged], list(range(1999, 2025)))
        self.assertEqual({r["dataset"] for r in merged if r["year"] <= 2020}, {"D77"})

    def test_web_export_parsed(self):
        text = "\n".join(['"Notes"\t"State"\t"State Code"\t"Year"\t"Year Code"\t"Deaths"\t"Population"\t"Crude Rate"\t"Age Adjusted Rate"',
                          '\t"Alabama"\t"01"\t"2021"\t"2021"\t5012\t5039877\t99.4\t71.2',
                          '\t"Alaska"\t"02"\t"2021"\t"2021"\tSuppressed\t732673\tSuppressed\tSuppressed',
                          '"Total"\t\t\t\t\t5012\t\t\t', '"---"', '"Dataset: Multiple Cause of Death, 2018-2024, Single Race"',
                          '"Multiple Cause of death: I50 (Heart failure)"', '"---"'])
        tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", encoding="utf-8"); tmp.write(text); tmp.close()
        exp = self.X.parse_web_export(tmp.name)
        os.remove(tmp.name)
        self.assertEqual((exp["db"], exp["grouping"], exp["codes"]), ("D157", "state", ["I50"]))
        self.assertEqual([r["group"] for r in exp["rows"]], ["Alabama", "Alaska"])
        self.assertEqual(exp["rows"][0]["aar"], 71.2)
        self.assertIn("deaths:suppressed", exp["rows"][1]["flags"])


if __name__ == "__main__":
    unittest.main()
