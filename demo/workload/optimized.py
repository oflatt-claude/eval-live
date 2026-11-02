"""Optimized implementation: the set configuration uses a set."""


def make_container(values, algorithm):
    return set(values) if algorithm == "set" else values
