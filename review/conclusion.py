from dataclasses import asdict, dataclass
from fractions import Fraction

from exchange.pair import StationPair


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    receipt_id: int
    piece_id: str
    station: str
    revision: int
    digest: str


@dataclass(frozen=True, slots=True)
class TurnaroundResult:
    pair_id: int
    start_station: str
    start_entry: str
    deadline: int
    by_deadline: Fraction
    eventual: Fraction
    sources: tuple[EvidenceReference, EvidenceReference]

    def to_record(self) -> dict:
        return {
            'pair_id': self.pair_id,
            'start_station': self.start_station,
            'start_entry': self.start_entry,
            'deadline': self.deadline,
            'by_deadline': str(self.by_deadline),
            'eventual': str(self.eventual),
            'sources': [asdict(source) for source in self.sources],
        }

    @classmethod
    def from_record(cls, record: dict) -> 'TurnaroundResult':
        return cls(
            record['pair_id'], record['start_station'], record['start_entry'], record['deadline'],
            Fraction(record['by_deadline']), Fraction(record['eventual']),
            tuple(EvidenceReference(**source) for source in record['sources']),
        )


def build_conclusion(
    pair: StationPair, start_station: str, start_entry: str, deadline: int,
    by_deadline: Fraction, eventual: Fraction,
) -> TurnaroundResult:
    if not isinstance(by_deadline, Fraction) or not isinstance(eventual, Fraction):
        raise ValueError('release probabilities must be exact fractions')
    if not 0 <= by_deadline <= eventual <= 1:
        raise ValueError('release probabilities are outside the full-batch bounds')
    if type(deadline) is not int or deadline < 0:
        raise ValueError('invalid report deadline')
    sources = []
    start_exists = False
    for piece in pair.pieces:
        artifact = piece.package.artifact
        identity = artifact.identity
        sources.append(EvidenceReference(piece.receipt_id, identity.piece_id, identity.station, identity.revision, piece.package.digest))
        if identity.station == start_station and any(entry.name == start_entry for entry in artifact.entries):
            start_exists = True
    if not start_exists:
        raise ValueError('report start is outside the selected pair')
    return TurnaroundResult(pair.pair_id, start_station, start_entry, deadline, by_deadline, eventual, tuple(sources))
