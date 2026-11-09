"""Shared CLI options for ordinary Python paper-evaluation scripts."""
from argparse import ArgumentParser, ArgumentTypeError
import math
import os
from pathlib import Path

# These affect selection/output rather than individual measurements. All are
# still saved in eval_arguments, including defaults.
CONTROL_ARGUMENTS = frozenset({
    "output", "data", "only", "benchmarks", "commit",
    "force", "dry_run", "render_only", "report", "timeout",
})


def positive_seconds(value: str) -> float:
    """Parse a finite, positive subprocess deadline in seconds."""
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ArgumentTypeError("must be a finite positive number of seconds")
    return result


def add_arguments(parser: ArgumentParser, *, output: str | Path = "out") -> ArgumentParser:
    """Add common options and return the parser for paper-specific additions.

    ``commit`` supplies the revision for requested treatments that omit it.
    Unknown script arguments affect cache identity unless explicitly excluded
    with ``Registry(selection_arguments=...)``. Every argument is saved.
    """
    parser.add_argument("--output", type=Path, default=Path(output), help="directory for artifacts, HTML, report.json, and report-data.json")
    parser.add_argument("--data", type=Path, default=None, help="JSON data/cache file (default: OUTPUT/results.json)")
    parser.add_argument("--only", nargs="+", default=None, metavar="ARTIFACT",
                        help="selected artifact IDs; one ID exports just that output and its data")
    parser.add_argument("--benchmarks", nargs="+", default=None, metavar="BENCHMARK", help="allowed benchmark names")
    parser.add_argument("--commit", default="HEAD", help="target Git commit/ref")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1, metavar="N",
                        help="maximum concurrent parallel=True treatments (default: CPU count); 1 runs sequentially")
    parser.add_argument("--timeout", type=positive_seconds, default=None, metavar="SECONDS",
                        help="per-command deadline (default: no limit unless configured on the benchmark)")
    parser.add_argument("--force", action="store_true", help="collect fresh observations, retaining history")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without builds or writes")
    parser.add_argument("--render-only", action="store_true",
                        help="replay bundled data with the current script; requires --report or --data and a different --output")
    parser.add_argument("--report", type=Path, default=None,
                        help="report.json to replay using only its sibling report-data.json")
    return parser
