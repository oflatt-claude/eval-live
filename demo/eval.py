#!/usr/bin/env python3
"""Run a complete small paper evaluation, reusing exact samples on later runs."""
from collections import defaultdict
from statistics import median

import eval_live


reg = eval_live.Registry(selection_arguments=["sizes", "repository", "baseline"],
                         title="Membership paper evaluation")


def narrow_summary(rows, data):
    keys = {(row["algorithm"], row["size"]) for row in rows}
    return {**data, "measurements": [row for row in data["measurements"]
            if (row["algorithm"], row["size"]) in keys]}


@reg.table("Summary", interactive_filter=narrow_summary,
           caption="Median time for 200 lookups; lower is better.")
def summary(data):
    """Compute medians from flat observations, including empty filtered data."""
    groups = defaultdict(list)
    for row in data["measurements"]:
        groups[row["algorithm"], row["size"]].append(row)
    if any(row["status"] == "timed-out" for row in data["measurements"]):
        eval_live.warn("Some membership treatments timed out; medians use completed measurements.")
    if any(row["status"] == "failure" for row in data["measurements"]):
        eval_live.error("Some membership treatments failed; inspect their saved process diagnostics.")
    result = []
    for (algorithm, size), rows in sorted(groups.items()):
        times = [row["seconds"] for row in rows if row["status"] == "success"]
        result.append({"algorithm": algorithm, "size": size, "samples": len(rows),
                       "completed": len(times), "timeouts": sum(row["status"] == "timed-out" for row in rows),
                       "median_ms": round(median(times) * 1000, 6) if times else None})
    return result


@reg.graph("Runtime")
def runtime(data):
    import matplotlib.pyplot as plt

    rows = [row for row in summary(data) if row["median_ms"] is not None]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    for algorithm in sorted({row["algorithm"] for row in rows}):
        selected = [row for row in rows if row["algorithm"] == algorithm]
        ax.plot([r["size"] for r in selected], [r["median_ms"] for r in selected],
                marker="o", label=algorithm)
    if rows:
        ax.legend()
    else:
        ax.text(0.5, 0.5, "No matching samples", ha="center", transform=ax.transAxes)
    ax.set(xlabel="Container size", ylabel="Median lookup time (ms)",
           title="List and set membership")
    fig.tight_layout()
    return fig


def history(data):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 3.5))
    for commit in dict.fromkeys(run["commit"] for run in data["eval_runs"]):
        run_ids = {run["run_id"] for run in data["eval_runs"] if run["commit"] == commit}
        selected = {**data, "measurements": [row for row in data["measurements"] if row["run_id"] in run_ids]}
        rows = [row for row in summary(selected) if row["median_ms"] is not None]
        if rows:
            ax.plot([r["size"] for r in rows], [r["median_ms"] for r in rows],
                    marker="o", label=commit[:8])
    if ax.lines:
        ax.legend()
    else:
        ax.text(0.5, 0.5, "No matching samples", ha="center", transform=ax.transAxes)
    ax.set(xlabel="Container size", ylabel="Median lookup time (ms)",
           title="The same configuration at two commits")
    fig.tight_layout()
    return fig


@reg.macros("Paper numbers", id="macros")
def numbers(data):
    rows = summary(data)
    return {"EvalSamples": len(data["measurements"]),
            "EvalInputSizes": len({row["size"] for row in rows}),
            "EvalBestMedianMs": min((row["median_ms"] for row in rows if row["median_ms"] is not None), default=0)}


def error_rows(treatment, status):
    return {"measurements": [{"size": treatment["size"], "algorithm": treatment["algorithm"],
                              "seed": treatment["seed"] + treatment["sample"], "sample": treatment["sample"],
                              "queries": 200, "found": None, "seconds": None, "status": status}]}


def membership(context):
    """Run the worker from the requested revision, not the evaluation checkout."""
    import json
    import sys

    result = context.command([
        sys.executable, "membership.py", str(context.config["size"]),
        context.config["algorithm"], str(context.config["seed"] + context.config["sample"]),
    ])
    return {"measurements": [{**json.loads(result.stdout), "sample": context.config["sample"], "status": "success"}]}


def main(argv=None):
    import argparse
    from workload import make_repository
    from pathlib import Path
    import sys

    parser = eval_live.add_arguments(argparse.ArgumentParser(description=__doc__),
                                     output=Path(__file__).resolve().parent / "out" / "paper")
    parser.add_argument("--sizes", nargs="+", type=int, default=[500, 2000, 8000])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--baseline", default="HEAD~1", help="baseline commit for the history figure")
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parent / "out" / "repository")
    parser.add_argument("--samples", type=int, default=3, help="repetitions per configuration")
    parser.add_argument("--parallel", action="store_true", help="allow concurrent treatments, limited by --jobs")
    tokens = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(tokens)
    if args.samples < 1:
        parser.error("--samples must be positive")
    if any(size <= 0 for size in args.sizes) or len(set(args.sizes)) != len(args.sizes):
        parser.error("--sizes must contain distinct positive integers")
    if not args.render_only:
        if args.dry_run and not (args.repository / ".git").exists():
            parser.error("run the demo once to create its workload repository before using --dry-run")
        if not args.dry_run:
            make_repository(args.repository)

    treatments = [
        {"size": size, "algorithm": algorithm, "seed": args.seed,
         "sample": sample, "parallel": args.parallel}
        for size in args.sizes
        for algorithm in ("list", "set")
        for sample in range(args.samples)
    ]
    reg.benchmark("membership", membership, version="4", error_rows=error_rows, inputs=["membership.py", "container.py"],
                  treatments=treatments, repository=args.repository)

    def comparison_treatments(treatments):
        return [request
                for treatment in treatments if treatment["algorithm"] == "set"
                for request in (treatment, {**treatment, "commit": args.baseline})]

    reg.graph("Commit comparison", history, id="history", requires=comparison_treatments)
    try:
        result = reg.run(args=args, argv=tokens)
    except (ValueError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Collected {result.collected}; reused {result.reused}. Data: {result.data_path}")
    if result.written:
        print(f"Report: {(args.output / 'index.html').resolve().as_uri()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
