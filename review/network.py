from dataclasses import dataclass
from fractions import Fraction

from exchange.pair import StationPair
from station.linear import solve_exact_system, states_reaching
from station.time_law import ZERO, RationalTimeLaw


@dataclass(frozen=True, slots=True)
class TimedHandoff:
    source: int
    target: int
    law: RationalTimeLaw


@dataclass(frozen=True, slots=True)
class ReleaseNetwork:
    entries: tuple[tuple[str, str], ...]
    releases: tuple[RationalTimeLaw, ...]
    handoffs: tuple[TimedHandoff, ...]


def compose_release_network(pair: StationPair) -> ReleaseNetwork:
    entry_keys = []
    entries = []
    for piece in pair.pieces:
        artifact = piece.package.artifact
        for entry in artifact.entries:
            entry_keys.append((piece.receipt_id, entry.name))
            entries.append((artifact.identity.station, entry.name, entry))
    indices = {key: index for index, key in enumerate(entry_keys)}
    targets = {
        (link.source_receipt, link.handoff_port): indices[(link.target_receipt, link.target_entry)]
        for link in pair.connections
    }
    releases = [ZERO for _ in entries]
    handoffs = []
    for source, (receipt_id, _) in enumerate(entry_keys):
        _, _, entry = entries[source]
        for destination, law in entry.exits:
            if destination.kind == 'release':
                releases[source] = law
            else:
                handoffs.append(TimedHandoff(source, targets[(receipt_id, destination.port)], law))
    names = tuple((station, name) for station, name, _ in entries)
    return ReleaseNetwork(names, tuple(releases), tuple(handoffs))


def solve_release_mass(direct: list[Fraction], transfers: list[tuple[int, int, Fraction]]) -> tuple[Fraction, ...]:
    active = sorted(states_reaching(
        (source for source, mass in enumerate(direct) if mass),
        ((source, target) for source, target, mass in transfers if mass),
    ))
    indices = {source: index for index, source in enumerate(active)}
    matrix = [[Fraction(row == col) for col in range(len(active))] for row in range(len(active))]
    for source, target, mass in transfers:
        if source in indices and target in indices:
            matrix[indices[source]][indices[target]] -= mass
    values = solve_exact_system(matrix, [direct[source] for source in active])
    result = [Fraction(0) for _ in direct]
    for source, value in zip(active, values):
        result[source] = value
    return tuple(result)
