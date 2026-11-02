#!/usr/bin/env python3
"""Run egglog on workload files from a Git repository."""
from collections import defaultdict
from statistics import median

import eval_live


reg = eval_live.Registry(selection_arguments=["files", "modes", "repository", "binary"],
                         title="Egglog evaluation")


@reg.table("Egglog timings", id="timings")
def timings(data):
    groups = defaultdict(list)
    for row in data["timings"]:
        groups[row["file"], row["mode"]].append(row["seconds"])
    return [{"file": file, "mode": mode, "samples": len(values),
             "median_ms": round(median(values) * 1000, 4)}
            for (file, mode), values in sorted(groups.items())]


@reg.macros("Paper numbers", id="numbers")
def numbers(data):
    return {"EgglogSamples": len(data["timings"])}


def measure(context):
    import json
    from pathlib import Path
    import tempfile

    flags = {"off": [], "proofs": ["--proofs"], "proof-testing": ["--proof-testing"]}
    with tempfile.TemporaryDirectory(prefix="eval-live-egglog-") as temporary:
        summary_path = Path(temporary) / "timing.json"
        result = context.command([
            context.config["binary"], "--mode", "no-messages", "-j", "1",
            "--timing-summary", summary_path,
            *flags[context.config["mode"]], context.config["file"],
        ])
        summary = json.loads(summary_path.read_text())
    if summary.get("schema_version") != 4:
        raise ValueError("this example expects egglog timing-summary schema 4")
    return {
        "timings": [{"file": context.config["file"], "mode": context.config["mode"],
                     "seconds": result.seconds, "sample": context.config["sample"], "timing_schema": summary["schema_version"]}],
        "phases": [{"phase": name, "nanoseconds": value}
                   for name, value in summary.items() if name.endswith("_ns")],
        "rulesets": summary["rulesets"],
    }


def main(argv=None):
    import argparse
    from pathlib import Path
    import sys

    parser = eval_live.add_arguments(argparse.ArgumentParser(description=__doc__),
                                     output=Path(__file__).resolve().parent / "out" / "egglog")
    parser.add_argument("--repository", type=Path, required=True, help="Git repository containing the .egg input files")
    parser.add_argument("--binary", type=Path, required=True, help="egglog executable to run on those files")
    parser.add_argument("--files", nargs="+", default=["egglog/tests/calc.egg"])
    parser.add_argument("--modes", nargs="+", choices=["off", "proofs", "proof-testing"],
                        default=["off", "proof-testing"])
    parser.add_argument("--samples", type=int, default=3, help="repetitions per configuration")
    parser.add_argument("--parallel", action="store_true", help="allow concurrent treatments, limited by --jobs")
    tokens = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(tokens)
    if args.samples < 1:
        parser.error("--samples must be positive")
    binary = args.binary.expanduser().resolve()
    treatments = [
        {"file": file, "mode": mode, "sample": sample, "binary": str(binary), "parallel": args.parallel}
        for file in args.files for mode in args.modes for sample in range(args.samples)
    ]
    reg.benchmark("egglog", measure, version="3", treatments=treatments,
                  repository=args.repository, inputs=[*args.files, binary], environment={"RUST_LOG": "error"})
    try:
        result = reg.run(args=args, argv=tokens)
    except (ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Collected {result.collected}; reused {result.reused}. Data: {result.data_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
