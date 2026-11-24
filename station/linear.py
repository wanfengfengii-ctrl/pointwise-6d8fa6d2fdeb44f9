from collections import defaultdict
from collections.abc import Iterable
from fractions import Fraction
from typing import TypeVar


T = TypeVar('T')
V = TypeVar('V')


class SingularSystemError(ValueError):
    pass


def states_reaching(seeds: Iterable[V], edges: Iterable[tuple[V, V]]) -> set[V]:
    incoming = defaultdict(list)
    for source, target in edges:
        incoming[target].append(source)
    reached = set(seeds)
    pending = list(reached)
    while pending:
        for source in incoming[pending.pop()]:
            if source not in reached:
                reached.add(source)
                pending.append(source)
    return reached


def solve_exact_system(matrix: list[list[T]], rhs: list[T]) -> list[T]:
    """Solve a square system over an exact field. Integers enter as fractions."""
    count = len(rhs)
    if len(matrix) != count or any(len(row) != count for row in matrix):
        raise ValueError('expected a square system and matching right-hand side')
    rows = [
        [Fraction(value) if type(value) is int else value for value in [*row, bound]]
        for row, bound in zip(matrix, rhs)
    ]
    for col in range(count):
        pivot = next((row for row in range(col, count) if rows[row][col]), None)
        if pivot is None:
            raise SingularSystemError('no unique solution for the supplied states')
        rows[col], rows[pivot] = rows[pivot], rows[col]
        pivot_value = rows[col][col]
        rows[col] = [value / pivot_value for value in rows[col]]
        for row in range(count):
            if row != col and rows[row][col]:
                weight = rows[row][col]
                rows[row] = [value - weight * basis for value, basis in zip(rows[row], rows[col])]
    return [row[-1] for row in rows]
