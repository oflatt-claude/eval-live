#!/usr/bin/env python3
"""Run a complete small paper evaluation, reusing exact samples on later runs."""
from collections import defaultdict
from statistics import median

import eval_live


def summary(data):
    """Compute medians from flat observations, including empty filtered data."""
    groups = defaultdict(list)
    for row in data["measurements"]:
        groups[row["algorithm"], row["size"]].append(row["seconds"])
    return [
        {"algorithm": algorithm, "size": size, "samples": len(times),
         "median_ms": round(median(times) * 1000, 6)}
        for (algorithm, size), times in sorted(groups.items())
    ]


def narrow_summary(rows, data):
    keys = {(row["algorithm"], row["size"]) for row in rows}
    return {**data, "measurements": [row for row in data["measurements"]
            if (row["algorithm"], row["size"]) in keys]}


def runtime(data):
    import matplotlib.pyplot as plt

    rows = summary(data)
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
    for name in ("baseline", "candidate"):
        selected = eval_live.select(data, name)
        rows = summary(selected)
        if rows:
            commit = selected["eval_runs"][0]["commit"][:8]
            ax.plot([r["size"] for r in rows], [r["median_ms"] for r in rows],
                    marker="o", label=f"{name} ({commit})")
    if ax.lines:
        ax.legend()
    else:
        ax.text(0.5, 0.5, "No matching samples", ha="center", transform=ax.transAxes)
    ax.set(xlabel="Container size", ylabel="Median lookup time (ms)",
           title="The same configuration at two commits")
    fig.tight_layout()
    return fig


def numbers(data):
    rows = summary(data)
    return {"EvalSamples": len(data["measurements"]),
            "EvalInputSizes": len({row["size"] for row in rows}),
            "EvalBestMedianMs": min((row["median_ms"] for row in rows), default=0)}


def membership(context):
    """Run the worker from the requested revision, not the evaluation checkout."""
    import json
    import sys

    result = context.command([
        sys.executable, "membership.py", str(context.config["size"]),
        context.config["algorithm"], str(context.config["seed"] + context.sample),
    ])
    return {"measurements": [json.loads(result.stdout)]}


def make_repository(path):
    """Create a tiny two-commit fixture, without a clone or external compiler."""
    import subprocess

    if (path / ".git").exists():
        return
    if path.exists() and any(path.iterdir()):
        raise ValueError(f"refusing to initialize nonempty demo repository: {path}")
    path.mkdir(parents=True, exist_ok=True)

    def git(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    worker = '''import json
import random
import sys
from time import perf_counter

size, algorithm, seed = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
values = list(range(size))
CONTAINER
rng = random.Random(seed)
queries = [rng.randrange(size * 2) for _ in range(200)]
started = perf_counter()
found = sum(value in container for value in queries)
seconds = perf_counter() - started
assert found == sum(value < size for value in queries)
print(json.dumps(dict(size=size, algorithm=algorithm, seed=seed,
                     queries=len(queries), found=found, seconds=seconds)))
'''
    git("init", "-b", "main")
    for message, container in (
        ("Baseline list membership", "container = values"),
        ("Use a set for the set configuration", "container = set(values) if algorithm == 'set' else values"),
    ):
        (path / "membership.py").write_text(worker.replace("CONTAINER", container), encoding="utf-8")
        git("add", "membership.py")
        git("-c", "user.name=eval-live demo", "-c", "user.email=demo@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-m", message)


def main(argv=None):
    import argparse
    from pathlib import Path
    import sys

    parser = eval_live.add_arguments(argparse.ArgumentParser(description=__doc__),
                                     output=Path(__file__).resolve().parent / "out" / "paper")
    parser.add_argument("--sizes", nargs="+", type=int, default=[500, 2000, 8000])
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--baseline", default="HEAD~1", help="baseline commit for the history figure")
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parent / "out" / "repository")
    tokens = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(tokens)
    if any(size <= 0 for size in args.sizes) or len(set(args.sizes)) != len(args.sizes):
        parser.error("--sizes must contain distinct positive integers")
    if not args.render_only:
        if args.dry_run and not (args.repository / ".git").exists():
            parser.error("run the demo once to create its fixture before using --dry-run")
        if not args.dry_run:
            make_repository(args.repository)

    def request(size, algorithm, commit):
        return eval_live.RunRequest("membership", repository=str(args.repository), commit=commit,
                                    config={"size": size, "algorithm": algorithm, "seed": args.seed},
                                    samples=args.samples)

    current = [request(size, algorithm, args.commit) for size in args.sizes for algorithm in ("list", "set")]
    reg = eval_live.Registry(selection_arguments=["sizes", "repository"],
                             browser_source=Path(__file__), title="Membership paper evaluation")
    reg.benchmark("membership", membership, version="1", inputs=["membership.py"])
    reg.graph("Runtime", runtime, requires={"current": current})
    reg.table("Summary", summary, requires={"current": current}, filter_source=narrow_summary,
              caption="Median time for 200 lookups; lower is better.")
    reg.graph("Commit comparison", history, id="history", requires={
        "baseline": [request(size, "set", args.baseline) for size in args.sizes],
        "candidate": [request(size, "set", args.commit) for size in args.sizes],
    })
    reg.macros("Paper numbers", numbers, requires={"current": current}, id="macros")
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
