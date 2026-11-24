from dataclasses import dataclass

from exchange.receipt import ReceivedPiece


class UnresolvedPortError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PortConnection:
    source_receipt: int
    handoff_port: str
    target_receipt: int
    target_entry: str


def resolve_connections(first: ReceivedPiece, second: ReceivedPiece) -> tuple[PortConnection, ...]:
    if first.package.artifact.identity.station == second.package.artifact.identity.station:
        raise UnresolvedPortError('a pair requires two different stations')
    connections = []
    for source, target in ((first, second), (second, first)):
        target_entries = {entry.name for entry in target.package.artifact.entries}
        handoff_ports = {
            destination.port
            for entry in source.package.artifact.entries
            for destination, _ in entry.exits
            if destination.kind == 'handoff'
        }
        for port in sorted(handoff_ports):
            if port not in target_entries:
                station = target.package.artifact.identity.station
                raise UnresolvedPortError(f'no entry {port!r} on paired station {station!r}')
            connections.append(PortConnection(source.receipt_id, port, target.receipt_id, port))
    return tuple(connections)
