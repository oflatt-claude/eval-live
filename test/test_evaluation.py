"""Integration checks for provenance, caching, Git isolation, and artifact plans.

Run: python -m unittest discover -s test -p 'test_evaluation.py'
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import eval_live
from eval_live.benchmarks import ProcessInterrupted, ProcessResult
from eval_live.store import atomic_write, parameter_rows, read_parameters

CALLS = []
BUILDS = []


def collect(context):
    CALLS.append((context.commit, dict(context.config), context.sample))
    if context.config.get("fail"):
        context.command([sys.executable, "-c", "import sys; print('diagnostic'); sys.exit(7)"])
    value = int((context.directory / "value.txt").read_text())
    return {"measurements": [{"value": value, "factor": context.config.get("factor", 1),
                               "sample": context.sample}]}


def slow(context):
    context.command([sys.executable, "-c", "import time; time.sleep(30)"])
    return {}


def build(context):
    BUILDS.append(context.commit)
    path = context.directory / "binary"
    path.write_text("built")
    return path


def use_binary(context):
    assert context.binary.read_text() == "built"
    return {"measurements": [{"value": context.config["value"]}]}


def invalid(context):
    return {"measurements": [{"nested": [1, 2]}]}


def mutate_input(context):
    (context.directory / "value.txt").write_text("changed")
    return {"measurements": [{"value": 99}]}


def table(view):
    return [{"value": row["value"], "sample": row.get("sample", 0)}
            for row in view.data["measurements"]]


def comparison(view):
    return [{"baseline": view["baseline"]["measurements"][0]["value"],
             "candidate": view["candidate"]["measurements"][0]["value"]}]


def macros(view):
    return {"PaperCount": len(view.data["measurements"]), "PaperLabel": "x_y & z"}


def figure(view):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.plot([row["value"] for row in view.data["measurements"]])
    return fig


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        BUILDS.clear()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.commit_value(10)
        self.old = self.git("rev-parse", "HEAD")
        self.commit_value(20)
        self.head = self.git("rev-parse", "HEAD")
        self.output = self.root / "output"
        self.data = self.output / "results.json"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL, text=True).strip()

    def commit_value(self, value):
        (self.repo / "value.txt").write_text(str(value))
        self.git("add", "value.txt")
        self.git("-c", "user.name=Tests", "-c", "user.email=tests@example.invalid", "-c", "commit.gpgsign=false", "commit", "-m", str(value))

    def request(self, *, samples=1, commit="HEAD", **config):
        return eval_live.RunRequest("read", repository=str(self.repo), commit=commit, config=config, samples=samples)

    def evaluation(self, request=None, artifacts=None, benchmark=None, **kwargs):
        return eval_live.Evaluation(
            benchmarks=[benchmark or eval_live.Benchmark("read", collect, "1", inputs=["value.txt"])],
            artifacts=artifacts or [eval_live.Table("values", {"data": request or self.request()}, table)], **kwargs)

    def run_eval(self, evaluation=None, *, render=False, **options):
        args = dict(output=self.output, data=self.data, **options)
        evaluation = evaluation or self.evaluation()
        if render:
            return evaluation.run(args=args, argv=["--test"])
        with patch("eval_live.planning.render_artifacts", return_value=[]):
            return evaluation.run(args=args, argv=["--test"])

    def stored(self):
        return eval_live.Store(self.data).data

    def test_cache_hits_do_not_collect_or_checkout(self):
        first = self.run_eval()
        self.assertEqual((first.collected, first.reused), (1, 0))
        with patch("eval_live.targets.Target.checkout", side_effect=AssertionError("cache hit must not check out")):
            second = self.run_eval()
        self.assertEqual((second.collected, second.reused), (0, 1))
        self.assertEqual(len(CALLS), 1)
        self.assertEqual(len(self.stored()["eval_invocations"]), 2)
        self.assertEqual(self.stored()["eval_argv"][0]["token"], "--test")

    def test_increased_samples_and_shared_requirements(self):
        request = self.request(samples=2)
        artifacts = [eval_live.Table("first", {"data": request}, table),
                     eval_live.Table("second", {"data": [request, self.request(samples=3)]}, table)]
        result = self.run_eval(self.evaluation(artifacts=artifacts))
        self.assertEqual(result.collected, 3)
        selected = self.stored()["eval_selections"]
        self.assertEqual(sum(row["artifact_id"] == "first" for row in selected), 2)
        self.assertEqual(sum(row["artifact_id"] == "second" for row in selected), 3)
        result = self.run_eval(self.evaluation(self.request(samples=5)))
        self.assertEqual((result.collected, result.reused), (2, 3))

    def test_selection_before_collection_and_invalid_subsets(self):
        artifacts = [eval_live.Table("first", {"data": self.request(factor=1)}, table),
                     eval_live.Table("second", {"data": self.request(factor=2)}, table)]
        self.run_eval(self.evaluation(artifacts=artifacts), only=["second"])
        self.assertEqual([call[1] for call in CALLS], [{"factor": 2}])
        with self.assertRaisesRegex(ValueError, "subset excludes"):
            self.run_eval(benchmarks=[])
        with self.assertRaisesRegex(ValueError, "unknown artifacts"):
            self.run_eval(only=["missing"])
        self.assertEqual(len(CALLS), 1)

    def test_configuration_inputs_and_unknown_arguments_invalidate(self):
        self.run_eval()
        self.run_eval(self.evaluation(self.request(factor=2)))
        self.run_eval(temperature=3)
        self.assertEqual(len(CALLS), 3)
        external = self.root / "input.txt"
        external.write_text("first")
        benchmark = eval_live.Benchmark("read", collect, "1", inputs=[external])
        self.run_eval(self.evaluation(benchmark=benchmark))
        external.write_text("second")
        self.run_eval(self.evaluation(benchmark=benchmark))
        self.assertEqual(len(CALLS), 5)
        result = self.run_eval(self.evaluation(benchmark=benchmark), only=["values"])
        self.assertEqual(result.collected, 0)

    def test_plot_changes_and_output_path_do_not_invalidate(self):
        self.run_eval()
        other = eval_live.Table("renamed", {"data": self.request()}, table, title="New label")
        with patch("eval_live.planning.render_artifacts", return_value=[]):
            result = self.evaluation(artifacts=[other]).run(args={"output": self.root / "elsewhere", "data": self.data}, argv=[])
        self.assertEqual(result.collected, 0)

    def test_commit_comparison_isolated_from_dirty_checkout(self):
        (self.repo / "value.txt").write_text("999")
        artifact = eval_live.Table("comparison", {"baseline": self.request(commit=self.old),
                                                  "candidate": self.request(commit=self.head)}, comparison)
        before = self.git("worktree", "list", "--porcelain")
        self.run_eval(self.evaluation(artifacts=[artifact]))
        self.assertEqual([row["value"] for row in self.stored()["measurements"]], [10, 20])
        self.assertEqual((self.repo / "value.txt").read_text(), "999")
        self.assertEqual(before, self.git("worktree", "list", "--porcelain"))

    def test_dirty_worktree_requires_opt_in_and_never_reuses(self):
        (self.repo / "value.txt").write_text("30")
        evaluation = self.evaluation(self.request(commit="WORKTREE"))
        with self.assertRaisesRegex(ValueError, "dirty"):
            self.run_eval(evaluation)
        self.run_eval(evaluation, allow_dirty=True)
        self.run_eval(evaluation, allow_dirty=True)
        self.assertEqual(len(CALLS), 2)
        self.assertTrue(all(not row["cacheable"] for row in self.stored()["eval_runs"]))

    def test_dry_run_never_creates_output_or_runs_benchmark(self):
        result = self.run_eval(dry_run=True)
        self.assertEqual(result.plan.missing, 1)
        self.assertFalse(self.output.exists())
        self.assertEqual(CALLS, [])

    def test_failures_checkpoint_and_independent_requests_continue(self):
        artifacts = [eval_live.Table("broken", {"data": self.request(fail=True, samples=3)}, table),
                     eval_live.Table("working", {"data": self.request()}, table)]
        with self.assertRaises(eval_live.CollectionError) as caught:
            self.run_eval(self.evaluation(artifacts=artifacts))
        self.assertEqual(caught.exception.result.collected, 1)
        data = self.stored()
        self.assertEqual([row["status"] for row in data["eval_runs"]], ["failure", "success"])
        self.assertEqual(data["eval_processes"][0]["returncode"], 7)
        self.assertIn("diagnostic", data["eval_processes"][0]["stdout"])
        self.assertEqual({row["artifact_id"] for row in data["eval_selections"]}, {"working"})
        self.assertEqual(self.run_eval().reused, 1)

    def test_timeout_and_invalid_rows_are_recorded_as_failures(self):
        benchmark = eval_live.Benchmark("read", slow, "1", timeout=0.02)
        with self.assertRaises(eval_live.CollectionError):
            self.run_eval(self.evaluation(benchmark=benchmark))
        self.assertEqual(self.stored()["eval_runs"][0]["status"], "timed-out")
        with self.assertRaises(eval_live.CollectionError):
            self.run_eval(self.evaluation(benchmark=eval_live.Benchmark("read", invalid, "1")))
        self.assertNotIn("measurements", self.stored())

    def test_mutating_materialized_inputs_cannot_produce_a_cache_hit(self):
        benchmark = eval_live.Benchmark("read", mutate_input, "1", inputs=["value.txt"])
        with self.assertRaisesRegex(eval_live.CollectionError, "inputs changed"):
            self.run_eval(self.evaluation(benchmark=benchmark))
        self.assertNotIn("measurements", self.stored())
        self.assertEqual(self.stored()["eval_runs"][0]["status"], "failure")

    def test_build_shared_across_configs_and_skipped_for_cache_hits(self):
        benchmark = eval_live.Benchmark("read", use_binary, "1", build=build, inputs=["value.txt"])
        requests = [self.request(value=1, samples=2), self.request(value=2)]
        evaluation = self.evaluation(artifacts=[eval_live.Table("values", {"data": requests}, table)], benchmark=benchmark)
        result = self.run_eval(evaluation)
        self.assertEqual(result.collected, 3)
        self.assertEqual(len(BUILDS), 1)
        self.assertTrue(all(row["binary_sha256"] for row in self.stored()["eval_runs"]))
        self.assertEqual(self.run_eval(evaluation).collected, 0)
        self.assertEqual(len(BUILDS), 1)

    def test_force_preserves_history_and_render_only_uses_saved_selection_without_git(self):
        original = self.run_eval()
        old_ids = {row["run_id"] for row in self.stored()["eval_runs"]}
        self.run_eval(force=True)
        self.assertEqual(len(CALLS), 2)
        self.repo.rename(self.root / "moved")
        with patch("eval_live.targets.Target.resolve", side_effect=AssertionError("must not use Git")):
            result = self.run_eval(render_only=True, invocation=original.invocation_id)
        ids = {row["run_id"] for row in self.stored()["eval_selections"] if row["invocation_id"] == result.invocation_id}
        self.assertEqual(ids, old_ids)
        self.assertEqual(result.collected, 0)

    def test_all_arguments_and_scalar_types_roundtrip(self):
        self.run_eval(empty=[], mixed=[None, True, 1, 1.0, "1"])
        args = read_parameters(self.stored()["eval_arguments"])
        self.assertEqual(args["empty"], [])
        self.assertEqual([type(v) for v in args["mixed"]], [type(None), bool, int, float, str])
        self.assertFalse(args["force"])
        self.assertEqual(args["output"], str(self.output))

    def test_corrupt_provenance_is_rejected_before_execution(self):
        self.run_eval()
        original = self.data.read_text()
        for table_name, field, value in (("eval_runs", "cache_key", "incorrect"),
                                          ("measurements", "value", [1, 2]),
                                          ("eval_selections", "run_id", "missing")):
            data = json.loads(original)
            data[table_name][0][field] = value
            self.data.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "invalid or incompatible"):
                self.run_eval()
            self.assertEqual(len(CALLS), 1)
        self.data.write_text(original)

    def test_interrupt_records_process_and_attempt(self):
        outcome = ProcessResult(("worker",), str(self.repo), 0.1, -9, "partial stdout", "", "interrupted")
        benchmark = eval_live.Benchmark("read", slow, "1")
        with patch("eval_live.benchmarks.run_command", side_effect=ProcessInterrupted(outcome)):
            with self.assertRaises(KeyboardInterrupt):
                self.run_eval(self.evaluation(benchmark=benchmark))
        data = self.stored()
        self.assertEqual(data["eval_invocations"][0]["status"], "interrupted")
        self.assertEqual(data["eval_runs"][0]["status"], "interrupted")
        self.assertEqual(data["eval_processes"][0]["stdout"], "partial stdout")

    def test_protocol_version_and_helper_code_invalidate_cache(self):
        self.run_eval()
        result = self.run_eval(self.evaluation(benchmark=eval_live.Benchmark("read", collect, "2", inputs=["value.txt"])))
        self.assertEqual(result.collected, 1)
        result = self.run_eval(self.evaluation(benchmark=eval_live.Benchmark("read", collect, "2", inputs=["value.txt"], dependencies=[table])))
        self.assertEqual(result.collected, 1)

    def test_exports_share_samples_and_write_only_one_json_data_file(self):
        request = self.request(samples=2)
        artifacts = [eval_live.Table("values", {"data": request}, table),
                     eval_live.Figure("figure", {"data": request}, figure),
                     eval_live.Macros("macros", {"data": request}, macros)]
        result = self.run_eval(self.evaluation(artifacts=artifacts), render=True)
        self.assertEqual(result.collected, 2)
        self.assertEqual({p.name for p in self.output.glob("*.json")}, {"results.json"})
        self.assertIn(r"\newcommand{\PaperCount}{2}", (self.output / "macros.tex").read_text())
        self.assertIn(r"x\_y \& z", (self.output / "macros.tex").read_text())
        self.assertTrue((self.output / "figure.pdf").read_bytes().startswith(b"%PDF"))
        page = (self.output / "index.html").read_text()
        self.assertIn("_eval_live_artifacts.py", page)
        self.assertIn("initEvalLive", page)


class StoreTests(unittest.TestCase):
    def test_parameters_roundtrip_and_invalid_values(self):
        for value in ({}, {"a": []}, {"a": None}, {"a": [1, "x", False, None, 1.0]}):
            self.assertEqual(read_parameters(parameter_rows(value)), value)
        for value in ({"a": {"b": 1}}, {"a": float("nan")}, {"a": [[1]]}):
            with self.assertRaises(ValueError):
                parameter_rows(value)

    def test_atomic_write_failure_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "data.json"
            path.write_text("original")
            with patch("eval_live.store.os.replace", side_effect=OSError("disk problem")):
                with self.assertRaises(OSError):
                    atomic_write(path, "replacement")
            self.assertEqual(path.read_text(), "original")
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_lock_schema_and_save_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "results.json"
            store = eval_live.Store(path)
            with self.assertRaises(RuntimeError):
                store.save()
            with store.writer():
                store.save()
                with self.assertRaisesRegex(RuntimeError, "another writer"):
                    with eval_live.Store(path).writer():
                        self.fail("second writer entered")
            data = json.loads(path.read_text())
            data["eval_metadata"][0]["schema_version"] = 99
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "incompatible"):
                eval_live.Store(path)


if __name__ == "__main__":
    unittest.main()
