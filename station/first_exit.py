from fractions import Fraction

from routes.model import RouteRevision
from routes.port_map import build_port_map
from station.artifact import EntryEvidence, ExitDestination
from station.linear import states_reaching
from station.time_law import ZERO, RationalTimeLaw


def _add(rows: dict, target, law: RationalTimeLaw) -> None:
    rows[target] = rows.get(target, ZERO).add(law)


def _local_equations(revision: RouteRevision):
    exits = {state: {} for state in revision.draft.steps}
    transfers = {state: {} for state in revision.draft.steps}
    for state, actions in revision.draft.steps.items():
        for action in actions:
            probability = action.probability
            if not probability or action.kind == 'hold':
                continue
            coefficients = (0, probability) if action.minutes else (probability,)
            law = RationalTimeLaw.from_polynomials(coefficients)
            if action.kind == 'step':
                _add(transfers[state], action.target, law)
            else:
                _add(exits[state], ExitDestination(action.kind, action.target), law)
    return transfers, exits


def _eliminate(states: list[str], transfers: dict, exits: dict) -> dict:
    """Eliminate workcells using only nonnegative series operations."""
    for index, state in enumerate(states):
        outgoing = transfers[state]
        loop = outgoing.pop(state, ZERO).repeat()
        for target, law in outgoing.items():
            outgoing[target] = loop.multiply(law)
        for destination, law in exits[state].items():
            exits[state][destination] = loop.multiply(law)
        for later in states[index + 1:]:
            incoming = transfers[later].pop(state, None)
            if incoming is None:
                continue
            for target, law in outgoing.items():
                _add(transfers[later], target, incoming.multiply(law))
            for destination, law in exits[state].items():
                _add(exits[later], destination, incoming.multiply(law))

    solved = {}
    for state in reversed(states):
        outputs = dict(exits[state])
        for target, transfer in transfers[state].items():
            for destination, law in solved[target].items():
                _add(outputs, destination, transfer.multiply(law))
        solved[state] = outputs
    return solved


def derive_first_exits(revision: RouteRevision) -> tuple[EntryEvidence, ...]:
    transfers, exits = _local_equations(revision)
    active = states_reaching(
        (state for state, destinations in exits.items() if destinations),
        ((state, target) for state, outgoing in transfers.items() for target in outgoing),
    )
    transfers = {
        state: {target: law for target, law in transfers[state].items() if target in active}
        for state in active
    }
    solved = _eliminate(sorted(active), transfers, exits)
    ports = build_port_map(revision.draft)
    entries = []
    for name in sorted(ports.entries):
        outputs = solved.get(ports.resolve(name), {})
        unexited = 1 - sum((law.total_mass() for law in outputs.values()), Fraction(0))
        if not 0 <= unexited <= 1:
            raise ValueError('invalid first-exit mass')
        entries.append(EntryEvidence(name, tuple(outputs.items()), unexited))
    return tuple(entries)
