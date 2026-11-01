"""Shared CLI options for ordinary Python paper-evaluation scripts."""
from argparse import ArgumentParser, ArgumentTypeError
import math
from pathlib import Path

# These affect selection/output rather than individual measurements. All are
# still saved in eval_arguments, including defaults.
CONTROL_ARGUMENTS = frozenset({
    "output", "data", "only", "benchmarks", "samples", "commit", "baseline",
    "force", "dry_run", "render_only", "invocation", "allow_dirty", "timeout",
})


def positive_int(value: str) -> int:
    """Parse a positive integer for sample counts or paper parameters."""
    result = int(value)
    if result <= 0:
        raise ArgumentTypeError("must be positive")
    return result


def positive_seconds(value: str) -> float:
    """Parse a finite, positive subprocess deadline in seconds."""
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ArgumentTypeError("must be a finite positive number of seconds")
    return result


def add_arguments(parser: ArgumentParser, *, output: str | Path = "out") -> ArgumentParser:
    """Add common options and return the parser for paper-specific additions.

    Scripts use ``args.commit`` and ``args.samples`` when declaring requests.
    Unknown script arguments affect cache identity unless explicitly excluded
    with ``Evaluation(selection_arguments=...)``. Every argument is saved.
    """
    parser.add_argument("--output", type=Path, default=Path(output), help="artifact directory")
    parser.add_argument("--data", type=Path, default=None, help="JSON data/cache file (default: OUTPUT/results.json)")
    parser.add_argument("--only", nargs="+", default=None, metavar="ARTIFACT", help="selected artifact IDs")
    parser.add_argument("--benchmarks", nargs="+", default=None, metavar="BENCHMARK", help="allowed benchmark names")
    parser.add_argument("--samples", type=positive_int, default=3, help="required samples per request")
    parser.add_argument("--commit", default="HEAD", help="target commit/ref, or WORKTREE")
    parser.add_argument("--timeout", type=positive_seconds, default=None, metavar="SECONDS",
                        help="per-command deadline (default: each Benchmark.timeout); increasing it retries cached timeouts")
    parser.add_argument("--force", action="store_true", help="collect fresh observations, retaining history")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without builds or writes")
    parser.add_argument("--render-only", action="store_true", help="render a saved invocation without Git or collection")
    parser.add_argument("--invocation", default=None, help="saved invocation to render (default: latest complete)")
    parser.add_argument("--allow-dirty", action="store_true", help="allow uncached measurements of a dirty WORKTREE")
    return parser
