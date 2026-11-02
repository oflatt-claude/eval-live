"""Exercise the public script, including a cold run, reuse, subsets, and replay."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DemoTests(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            repo = directory / "repository"
            output = directory / "paper"
            args = ["--repository", str(repo), "--output", str(output),
                    "--sizes", "20", "50", "--samples", "1"]

            def run(*extra):
                completed = subprocess.run([sys.executable, str(ROOT / "demo/eval.py"), *args, *extra],
                                           capture_output=True, text=True, timeout=60)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                return completed.stdout

            self.assertIn("Collected 6; reused 0", run())
            data_path = output / "results.json"
            initial = json.loads(data_path.read_text())
            self.assertEqual(len(initial["eval_runs"]), 6)
            self.assertEqual({row["status"] for row in initial["eval_invocations"]}, {"success"})
            self.assertEqual([row["token"] for row in initial["eval_argv"]], args)
            self.assertIn("Collected 0; reused 6", run())
            self.assertIn("Collected 0; reused 2", run("--only", "summary", "--sizes", "20"))
            self.assertIn("Collected 6; reused 0", run("--seed", "7"))
            self.assertIn("Collected 12; reused 0", run("--samples", "2"))
            self.assertIn("Collected 0; reused 12", run("--samples", "2"))
            repo.rename(directory / "removed-repository")
            self.assertIn("Collected 0; reused 12", run("--render-only"))
            self.assertEqual({path.name for path in output.glob("*.json")}, {"results.json"})
            self.assertEqual({path.name for path in output.glob("*.pdf")}, {"runtime.pdf", "history.pdf"})
            self.assertTrue((output / "summary.tex").is_file())
            self.assertTrue((output / "macros.tex").is_file())
            self.assertTrue((output / "index.html").is_file())
            final = json.loads(data_path.read_text())
            self.assertEqual(len(final["eval_runs"]), 24)
            for rows in final.values():
                for row in rows:
                    self.assertTrue(all(value is None or type(value) in (str, int, float, bool)
                                        for value in row.values()))


if __name__ == "__main__":
    unittest.main()
