"""Unit tests for scripts/gate.py output-parsing helpers (no Django needed)."""

import importlib.util
import unittest
from pathlib import Path

GATE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("verdict_gate", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


class ParseTestOutputTests(unittest.TestCase):
    def test_ok_summary(self):
        summary = gate.parse_test_output("Ran 42 tests in 1.234s\n\nOK\n")
        self.assertEqual(summary.ran, 42)
        self.assertEqual(summary.failures, 0)
        self.assertEqual(summary.errors, 0)
        self.assertEqual(tuple(summary.failed_tests), ())

    def test_failed_summary_and_names(self):
        text = (
            "FAIL: test_a (tests.test_x.Foo)\n"
            "ERROR: test_b (tests.test_y.Bar)\n"
            "Ran 10 tests in 0.500s\n\nFAILED (failures=1, errors=1)\n"
        )
        summary = gate.parse_test_output(text)
        self.assertEqual((summary.ran, summary.failures, summary.errors), (10, 1, 1))
        self.assertEqual(
            list(summary.failed_tests),
            ["test_a (tests.test_x.Foo)", "test_b (tests.test_y.Bar)"],
        )

    def test_empty_output(self):
        summary = gate.parse_test_output("")
        self.assertEqual((summary.ran, summary.failures, summary.errors), (0, 0, 0))
        self.assertEqual(list(summary.failed_tests), [])


class ParseAcceptanceOutputTests(unittest.TestCase):
    REPORT = (
        "DOGFOOD 2026 acceptance report\n"
        "portal: http://localhost:8080\n"
        "claimed: T1 T2\n"
        "fixtures: fixtures.json\n"
        "\n"
        "T1  gallery is public ................. PASS\n"
        "T1  project from fixtures shown ....... PASS\n"
        "T1  closed event refuses submissions .. PASS\n"
        "T2  judge sees own scores ............. PASS\n"
        "T2  judge cannot see peer scores ...... PASS\n"
        "T2  participant blocked ............... PASS\n"
        "T2  csv export works .................. PASS\n"
        "\n"
        "claimed T1 T2, verified T1 T2\n"
    )

    def test_all_pass(self):
        acc = gate.parse_acceptance_output(self.REPORT)
        self.assertEqual((acc.passed, acc.total), (7, 7))
        self.assertEqual(acc.verified, "claimed T1 T2, verified T1 T2")

    def test_one_fail(self):
        text = self.REPORT.replace(
            "csv export works .................. PASS",
            "csv export works .................. FAIL",
        )
        acc = gate.parse_acceptance_output(text)
        self.assertEqual((acc.passed, acc.total), (6, 7))
        self.assertIn("verified", acc.verified)


class FormatStepTests(unittest.TestCase):
    def test_compact_line(self):
        line = gate.format_step("check", True, "0 issues", 2.345)
        self.assertIn("check", line)
        self.assertIn("PASS", line)
        self.assertIn("0 issues", line)
        self.assertRegex(line, r"\(\d+\.\d+s\)")

    def test_fail_status(self):
        line = gate.format_step("migrations", False, "rc=1 ...", 0.05)
        self.assertIn("FAIL", line)


if __name__ == "__main__":
    unittest.main()
