# Tests for the smarter brain: the exact calculator (smart.calc), the knowledge base search (knowledge.py),
# learning from corrections (lessons.py) and the easy/hard routing guards (intent.py).
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import intent
import knowledge
import lessons
import smart


class CalcTests(unittest.TestCase):
    def test_exact_maths(self):
        self.assertEqual(smart.calc("70*15/3"), 350)
        self.assertEqual(smart.calc("2^10"), 1024)
        self.assertAlmostEqual(smart.calc("sqrt(16)+log10(1000)"), 7)

    def test_never_runs_code(self):
        for bad in ("__import__('os').system('dir')", "open('x')", "9**999999", "a+b"):
            with self.assertRaises(Exception):
                smart.calc(bad)


class KnowledgeTests(unittest.TestCase):
    def test_search_finds_the_right_passage(self):
        old = knowledge._index, knowledge._bm25
        knowledge._index = {"files": {
            "a": {"label": "Lecture", "title": "Renal pharmacology", "chunks": [{"text": "Loop diuretics such as furosemide inhibit the NKCC2 transporter and cause hypokalemia.", "where": "03:10"}]},
            "b": {"label": "Upload", "title": "Reproduction", "chunks": [{"text": "Oxytocin stimulates uterine contractions during labour; cervical stretch triggers its release.", "where": "p.75"}]},
        }}
        knowledge._bm25 = None
        try:
            hits = knowledge.search("what do diuretics do to potassium (hypokalemia)?")
            self.assertEqual(hits[0]["title"], "Renal pharmacology")
            self.assertEqual(hits[0]["where"], "03:10")
            self.assertEqual(knowledge.search("oxytocin labour")[0]["title"], "Reproduction")
            self.assertEqual(knowledge.search("quantum chromodynamics"), [])
        finally:
            knowledge._index, knowledge._bm25 = old

    def test_stemming(self):
        self.assertEqual(knowledge.tokens("diuretics"), knowledge.tokens("diuretic"))


class LessonTests(unittest.TestCase):
    def setUp(self):
        self._file = lessons.LESSONS_FILE
        lessons.LESSONS_FILE = os.path.join(tempfile.mkdtemp(), "lessons.json")

    def tearDown(self):
        lessons.LESSONS_FILE = self._file

    def test_corrections_are_kept_once_and_found(self):
        lessons.add_correction("When Sam says 'the portal' he means the university student portal", "no I meant the uni portal")
        lessons.add_correction("When Sam says 'the portal' he means the university student portal")
        lessons.add_correction("Sam wants shorter answers")
        self.assertEqual(len(lessons.corrections()), 2)
        self.assertIn("When Sam says 'the portal' he means the university student portal", lessons.relevant_for("open the portal"))


class RoutingTests(unittest.TestCase):
    def test_maths_and_own_material_are_hard(self):
        for t in ("a 70 kg patient needs 15 mg/kg split into 3 doses, how much per dose", "what is 17 * 23",
                  "quiz me on my notes", "what did my lecture say about labour"):
            self.assertTrue(intent._MATH_RE.search(t) or intent._MATERIAL_RE.search(t), t)
        for t in ("hey whats up", "I have 3 tasks today"):
            self.assertFalse(intent._MATH_RE.search(t) or intent._MATERIAL_RE.search(t), t)


if __name__ == "__main__":
    unittest.main()
