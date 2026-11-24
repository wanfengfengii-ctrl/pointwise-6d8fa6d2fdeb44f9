import json
from dataclasses import dataclass
from fractions import Fraction

from station.artifact import ArtifactIdentity, EntryEvidence, ExitDestination, StationArtifact, encode_evidence, evidence_digest
from station.time_law import RationalTimeLaw


class PackageError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StationPackage:
    artifact: StationArtifact
    digest: str
    original: bytes


def _name(value) -> str:
    if not isinstance(value, str) or not value:
        raise PackageError('expected a nonempty station or port name')
    return value


def _entry(record: dict) -> EntryEvidence:
    name = _name(record['name'])
    unexited_text = record['unexited']
    if type(unexited_text) is not str:
        raise PackageError('unexited mass must be an exact string')
    unexited = Fraction(unexited_text)
    if not 0 <= unexited <= 1 or not isinstance(record['exits'], list):
        raise PackageError('invalid first-exit masses')
    outputs = {}
    for item in record['exits']:
        if not isinstance(item, list) or len(item) != 3:
            raise PackageError('invalid exit record')
        kind, port, program = item
        if kind == 'handoff':
            _name(port)
        elif kind != 'release' or port is not None:
            raise PackageError('invalid exit destination')
        destination = ExitDestination(kind, port)
        if destination in outputs:
            raise PackageError('duplicate exit destination')
        law = RationalTimeLaw.from_wire(program)
        if not 0 < law.total_mass() <= 1:
            raise PackageError('exit mass must be positive and at most one')
        outputs[destination] = law
    if unexited + sum(law.total_mass() for law in outputs.values()) != 1:
        raise PackageError('first exits and unexited mass must account for the whole batch')
    return EntryEvidence(name, tuple(outputs.items()), unexited)


def decode_package(raw: bytes) -> StationPackage:
    if not isinstance(raw, bytes):
        raise PackageError('original evidence must be bytes')
    try:
        envelope = json.loads(raw)
        body = envelope['body']
        if type(body['format']) is not int or body['format'] != 1:
            raise PackageError('unsupported evidence format')
        revision = body['revision']
        if type(revision) is not int or not 0 < revision < 2**63:
            raise PackageError('invalid evidence revision')
        identity = ArtifactIdentity(_name(body['station']), revision)
        if body['piece_id'] != identity.piece_id:
            raise PackageError('piece identity does not match its origin')
        digest = envelope['digest']
        if digest != evidence_digest(body):
            raise PackageError('evidence digest mismatch')
        if not isinstance(body['entries'], list) or not body['entries']:
            raise PackageError('no public entries')
        entries = tuple(_entry(record) for record in body['entries'])
        if len({entry.name for entry in entries}) != len(entries):
            raise PackageError('duplicate public entry')
        artifact = StationArtifact(identity, entries)
        if encode_evidence(artifact) != raw:
            raise PackageError('noncanonical or unexpected evidence fields')
        return StationPackage(artifact, digest, raw)
    except PackageError:
        raise
    except (KeyError, TypeError, ValueError, ZeroDivisionError, RecursionError) as error:
        raise PackageError('malformed station evidence') from error
