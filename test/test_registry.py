"""Registry presentation checks for styled cells, console output, and file export."""
import io
import json
from pathlib import Path
import tempfile
import unittest

from rich.console import Console

import eval_live
from eval_live import latex


def ratios(_data):
    return [
        {"file": "a.egg", "off": "1.000x", "result": {"text": "faster", "style": {"color": "green"}}},
        {"file": "b.egg", "off": "2.000x", "result": {"text": "slower", "style": {"color": "red", "bold": True}}},
    ]


class RegistryPresentationTests(unittest.TestCase):
    def setUp(self):
        self.reg = eval_live.Registry()
        self.reg.table("Ratios", ratios, caption="Ratio is target / baseline.")
        self.reg.table("Empty", lambda data: [])

    def test_table_results_include_rows_and_presentation_metadata(self):
        results = self.reg.run_tables({})
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], {"name": "Ratios", "rows": ratios({}),
                                     "caption": "Ratio is target / baseline.", "has_interactive_filter": False})
        self.assertEqual(results[1]["rows"], [])

    def test_latex_unwraps_styles_and_escapes_headers_and_values(self):
        rows = [{"a&b": {"text": "x_y%", "style": {"color": "green"}}}, {}]
        text = latex.table(rows, ["a&b"])
        self.assertIn(r"a\&b", text)
        self.assertIn(r"x\_y\%", text)
        self.assertNotIn("style", text)
        self.assertEqual(latex.escape(None), "")
        self.assertEqual(latex.escape(r"\{}#$~^"),
                         r"\textbackslash{}\{\}\#\$\textasciitilde{}\textasciicircum{}")

    def test_console_prints_selected_nonempty_tables(self):
        stream = io.StringIO()
        console = Console(file=stream, color_system=None, width=100)
        self.assertIs(self.reg.render_to_console({}, console=console), console)
        self.assertIn("Ratio is target / baseline.", stream.getvalue())
        self.assertIn("faster", stream.getvalue())
        self.assertNotIn("Empty", stream.getvalue())
        for selection in ([], ["Empty"]):
            stream.seek(0)
            stream.truncate()
            self.reg.render_to_console({}, tables=selection, console=console)
            self.assertEqual(stream.getvalue(), "")
        with self.assertRaisesRegex(ValueError, "unknown table"):
            self.reg.render_to_console({}, tables=["Nope"], console=console)

    def test_directory_export_preserves_styled_json_and_omits_empty_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            written = self.reg.render_to_dir({}, directory)
            self.assertEqual({Path(path).name for path in written}, {"ratios.json", "ratios.tex"})
            self.assertEqual(json.loads((Path(directory) / "ratios.json").read_text()), ratios({}))
            tex = (Path(directory) / "ratios.tex").read_text()
            self.assertIn("file & off & result", tex)
            self.assertIn("faster", tex)
            self.assertNotIn("'text'", tex)


if __name__ == "__main__":
    unittest.main()
