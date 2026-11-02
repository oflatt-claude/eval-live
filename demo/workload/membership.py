"""Measure membership lookups using this revision's container implementation."""
import json
import random
import sys
from time import perf_counter

from container import make_container


def main():
    size, algorithm, seed = int(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    values = list(range(size))
    container = make_container(values, algorithm)
    rng = random.Random(seed)
    queries = [rng.randrange(size * 2) for _ in range(200)]
    started = perf_counter()
    found = sum(value in container for value in queries)
    seconds = perf_counter() - started
    assert found == sum(value < size for value in queries)
    print(json.dumps(dict(size=size, algorithm=algorithm, seed=seed,
                         queries=len(queries), found=found, seconds=seconds)))


if __name__ == "__main__":
    main()
