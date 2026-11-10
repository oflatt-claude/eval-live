#!/usr/bin/env python3
"""Build egglog and run workload files from the same Git commit."""
from collections import defaultdict
from statistics import median

import eval_live


reg = eval_live.Registry(selection_arguments=["files", "modes", "repository"],
                         title="Egglog evaluation")


@reg.table("Egglog timings")
def timings(data):
    groups = defaultdict(list)
    for row in data["timings"]:
        groups[row["file"], row["mode"]].append(row)
    if any(row["status"] == "timed-out" for row in data["timings"]):
        eval_live.warn("Some egglog treatments timed out; medians use completed measurements.")
    if any(row["status"] == "failure" for row in data["timings"]):
        eval_live.error("Some egglog treatments failed; inspect their saved process diagnostics.")
    result = []
    for (file, mode), rows in sorted(groups.items()):
        values = [row["seconds"] for row in rows if row["status"] == "success"]
        result.append({"file": file, "mode": mode, "samples": len(rows), "completed": len(values),
                       "timeouts": sum(row["status"] == "timed-out" for row in rows),
                       "median_ms": round(median(values) * 1000, 4) if values else None})
    return result


@reg.macros("Paper numbers", id="numbers")
def numbers(data):
    return {"EgglogSamples": len(data["timings"])}


def error_rows(treatment, status):
    return {"timings": [{"file": treatment["file"], "mode": treatment["mode"],
                         "sample": treatment["sample"], "seconds": None, "timing_schema": None, "status": status}],
            "phases": [{"phase": None, "nanoseconds": None, "status": status}],
            "rulesets": [{"status": status}]}


def build(context):
    import os

    context.command(["cargo", "build", "--locked", "--release", "-p", "egglog", "--bin", "egglog",
                     "--target-dir", "target"])
    return context.directory / "target" / "release" / ("egglog.exe" if os.name == "nt" else "egglog")


def measure(context):
    import json
    from pathlib import Path
    import tempfile

    flags = {"off": [], "proofs": ["--proofs"], "proof-testing": ["--proof-testing"]}
    with tempfile.TemporaryDirectory(prefix="eval-live-egglog-") as temporary:
        summary_path = Path(temporary) / "timing.json"
        result = context.command([
            context.binary, "--mode", "no-messages", "-j", "1",
            "--timing-summary", summary_path,
            *flags[context.config["mode"]], context.config["file"],
        ])
        summary = json.loads(summary_path.read_text())
    if summary.get("schema_version") != 4:
        raise ValueError("this example expects egglog timing-summary schema 4")
    return {
        "timings": [{"file": context.config["file"], "mode": context.config["mode"],
                     "seconds": result.seconds, "sample": context.config["sample"], "timing_schema": summary["schema_version"],
                     "status": "success"}],
        "phases": [{"phase": name, "nanoseconds": value}
                   for name, value in summary.items() if name.endswith("_ns")],
        "rulesets": summary["rulesets"] or [{"status": "success"}],
    }


def main(argv=None):
    import argparse
    from pathlib import Path
    import sys

    parser = eval_live.add_arguments(argparse.ArgumentParser(description=__doc__),
                                     output=Path(__file__).resolve().parent / "out" / "egglog")
    measurements = parser.add_argument_group('Egglog parameters (normal runs only)')
    measurements.add_argument("--repository", type=Path, help="Git repository containing egglog's Cargo workspace and .egg files")
    measurements.add_argument("--files", nargs="+", default=["egglog/tests/calc.egg"])
    measurements.add_argument("--modes", nargs="+", choices=["off", "proofs", "proof-testing"],
                        default=["off", "proof-testing"])
    measurements.add_argument("--samples", type=int, default=3, help="repetitions per configuration")
    measurements.add_argument("--parallel", action="store_true", help="allow concurrent treatments, limited by --jobs")
    tokens = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(tokens)
    if not args.replay and args.repository is None:
        parser.error("--repository is required for collection")
    if args.samples < 1:
        parser.error("--samples must be positive")
    treatments = [
        {"file": file, "mode": mode, "sample": sample, "parallel": args.parallel}
        for file in args.files for mode in args.modes for sample in range(args.samples)
    ]
    reg.benchmark("egglog", measure, version="5", error_rows=error_rows, treatments=treatments,
                  repository=args.repository or ".", build=build, environment={"RUST_LOG": "error"})
    try:
        reg.run(args=args, argv=tokens)
    except (ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
