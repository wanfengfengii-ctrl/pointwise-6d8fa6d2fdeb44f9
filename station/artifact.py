from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256

from routes.merger import MergeProvenance
from station.time_law import RationalTimeLaw
from storage import canonical_json


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    station: str
    revision: int

    @property
    def piece_id(self) -> str:
        return sha256(canonical_json([self.station, self.revision])).hexdigest()


@dataclass(frozen=True, slots=True)
class ExitDestination:
    kind: str
    port: str | None = None


@dataclass(frozen=True, slots=True)
class EntryEvidence:
    name: str
    exits: tuple[tuple[ExitDestination, RationalTimeLaw], ...]
    unexited: Fraction


@dataclass(frozen=True, slots=True)
class StationArtifact:
    identity: ArtifactIdentity
    entries: tuple[EntryEvidence, ...]
    merge: MergeProvenance | None = None


def merge_block(merge: MergeProvenance) -> dict:
    return {
        'station': merge.station,
        'revision': merge.revision,
        'parent': merge.parent,
        'left': merge.left,
        'right': merge.right,
        'decisions': [
            [index, category, field, side]
            for index, (category, _name, field, side) in enumerate(merge.decisions, 1)
        ],
    }


def _evidence_body(artifact: StationArtifact) -> dict:
    identity = artifact.identity
    body = {
        'piece_id': identity.piece_id,
        'station': identity.station,
        'revision': identity.revision,
        'entries': [
            {
                'name': entry.name,
                'unexited': str(entry.unexited),
                'exits': [
                    [destination.kind, destination.port, law.to_wire()]
                    for destination, law in sorted(entry.exits, key=lambda pair: (pair[0].kind, pair[0].port or ''))
                ],
            }
            for entry in sorted(artifact.entries, key=lambda entry: entry.name)
        ],
    }
    if artifact.merge is None:
        body = {'format': 1, **body}
    else:
        body = {'format': 2, 'merge': merge_block(artifact.merge), **body}
    return body


def evidence_digest(body: dict) -> str:
    return sha256(canonical_json(body)).hexdigest()


def encode_evidence(artifact: StationArtifact) -> bytes:
    body = _evidence_body(artifact)
    return canonical_json({'body': body, 'digest': evidence_digest(body)})
