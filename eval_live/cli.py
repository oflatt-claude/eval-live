"""Shared CLI options for ordinary Python paper-evaluation scripts."""
from argparse import ArgumentParser, ArgumentTypeError, Namespace
import math
import os
from pathlib import Path

# All arguments are recorded; these do not identify individual measurements.
OUTPUT_ARGUMENTS = frozenset({"output", "only", "replay", "no_browser"})
CONTROL_ARGUMENTS = OUTPUT_ARGUMENTS | {"cache", "commit", "force", "dry_run", "timeout"}


def positive_seconds(value: str) -> float:
    """Parse a finite, positive subprocess deadline in seconds."""
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ArgumentTypeError("must be a finite positive number of seconds")
    return result


def _output_arguments(parser, output="out"):
    parser.add_argument("--output", type=Path, default=Path(output), metavar="DIR",
                        help="dedicated flat report directory; existing files are cleared before running (default: %(default)s)")
    parser.add_argument("--only", nargs="+", default=None, metavar="ID",
                        help="generate only these output IDs (default: all)")
    parser.add_argument("--replay", type=Path, default=None, metavar="FOLDER",
                        help="use only this folder's reproduce.json and results.json with the current rendering code")
    parser.add_argument("--no-browser", action="store_true", help="save the report without opening a browser")


def validate_replay(args, argv):
    """Reject explicitly supplied collection options before reading a replay bundle."""
    if isinstance(args, Namespace):
        parser = ArgumentParser(add_help=False, allow_abbrev=False)
        _output_arguments(parser)
        _, extra = parser.parse_known_args(argv)
    else:
        extra = ["--" + key.replace("_", "-") for key in args if key not in OUTPUT_ARGUMENTS]
    if extra:
        raise ValueError("--replay accepts only --only, --output, and --no-browser; "
                         f"remove: {' '.join(extra)}. Run without --replay to change measurements.")


def add_arguments(parser: ArgumentParser, *, output: str | Path = "out",
                  cache: str | Path | None = None) -> ArgumentParser:
    """Add common options and return the parser for paper-specific additions.

    ``cache`` defaults to ``cache.json`` beside the declared default ``output``;
    overriding ``--output`` does not move it. ``commit`` supplies the revision
    for requested treatments that omit it. Script arguments affect cache
    identity unless excluded with ``Registry(selection_arguments=...)``.
    Every argument is saved. Replay permits only output controls; pass the
    parsed Namespace and original tokens to ``Registry.run``. Option names
    must be spelled in full.
    """
    parser.allow_abbrev = False
    _output_arguments(parser.add_argument_group("Report and replay"), output)
    collection = parser.add_argument_group("Collection (normal runs only)")
    collection.add_argument("--cache", type=Path,
                            default=Path(cache) if cache is not None else Path(output).parent / "cache.json",
                            metavar="FILE", help="measurement cache (default: %(default)s); independent of --output")
    collection.add_argument("--commit", default="HEAD", metavar="REF", help="default workload commit (default: HEAD)")
    collection.add_argument("--jobs", type=int, default=os.cpu_count() or 1, metavar="N",
                            help="maximum concurrent parallel=True treatments (default: CPU count); 1 runs sequentially")
    collection.add_argument("--timeout", type=positive_seconds, default=None, metavar="SECONDS",
                            help="per-command deadline (default: no limit unless configured on the benchmark)")
    collection.add_argument("--force", action="store_true", help="collect fresh observations, retaining history")
    collection.add_argument("--dry-run", action="store_true", help="show the detailed plan without builds or writes")
    return parser
