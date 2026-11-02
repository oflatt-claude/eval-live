#!/usr/bin/env python3
"""A small egglog-encoding evaluation using a supplied, fingerprinted binary.

The Git commit selects workload sources. The prebuilt engine is a separately
hashed input; this example does not claim it was built from that workload commit.
"""
from collections import defaultdict
from statistics import median

import eval_live


def timings(data):
    groups = defaultdict(list)
    for row in data["timings"]:
        groups[row["file"], row["treatment"]].append(row["seconds"])
    return [{"file": file, "treatment": treatment, "samples": len(values),
             "median_ms": round(median(values) * 1000, 4)}
            for (file, treatment), values in sorted(groups.items())]


def numbers(data):
    return {"EgglogSamples": len(data["timings"])}


def prebuilt(context):
    from pathlib import Path
    return Path(context.config["binary"])


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
            *flags[context.config["treatment"]], context.config["file"],
        ])
        summary = json.loads(summary_path.read_text())
    if summary.get("schema_version") != 4:
        raise ValueError("this example expects egglog timing-summary schema 4")
    return {
        "timings": [{"file": context.config["file"], "treatment": context.config["treatment"],
                     "seconds": result.seconds, "timing_schema": summary["schema_version"]}],
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
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--files", nargs="+", default=["egglog/tests/calc.egg"])
    parser.add_argument("--treatments", nargs="+", choices=["off", "proofs", "proof-testing"],
                        default=["off", "proof-testing"])
    tokens = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(tokens)
    binary = args.binary.expanduser().resolve()
    reg = eval_live.Registry(selection_arguments=["files", "treatments", "repository", "binary"],
                             browser_source=Path(__file__), title="Egglog evaluation")
    for file in args.files:
        reg.benchmark(file, measure, version="1", build=prebuilt, build_config={"binary": str(binary)},
                      inputs=[file, binary], environment={"RUST_LOG": "error"})
    requests = [eval_live.RunRequest(file, repository=str(args.repository), commit=args.commit,
                                    config={"file": file, "treatment": treatment}, samples=args.samples)
                for file in args.files for treatment in args.treatments]
    reg.table("Egglog timings", timings, requires={"runs": requests}, id="timings")
    reg.macros("Paper numbers", numbers, requires={"runs": requests}, id="numbers")
    try:
        result = reg.run(args=args, argv=tokens)
    except (ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Collected {result.collected}; reused {result.reused}. Data: {result.data_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
