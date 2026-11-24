import json
from dataclasses import dataclass
from fractions import Fraction

from routes.merger import MergeProvenance
from station.artifact import (
    ArtifactIdentity,
    EntryEvidence,
    ExitDestination,
    StationArtifact,
    encode_evidence,
    evidence_digest,
    merge_block,
)
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


def _merge_provenance(body: dict, identity: ArtifactIdentity) -> MergeProvenance:
    merge = body['merge']
    if not isinstance(merge, dict):
        raise PackageError('merge provenance must be an object')
    station = _name(merge.get('station'))
    for key in ('revision', 'parent', 'left', 'right'):
        value = merge.get(key)
        if type(value) is not int or not 0 < value < 2**63:
            raise PackageError(f'merge provenance field {key!r} must be a positive revision id')
    parent, left, right, revision = merge['parent'], merge['left'], merge['right'], merge['revision']
    if station != identity.station or revision != identity.revision:
        raise PackageError('merge provenance is not bound to the piece identity')
    if left == right or not (parent < left < revision and parent < right < revision):
        raise PackageError('merge provenance revision ordering is invalid')
    records = merge.get('decisions')
    if not isinstance(records, list):
        raise PackageError('merge decisions must be a list')
    decoded = []
    expected_id = 1
    allowed = {('cell', 'name'), ('cell', 'actions'), ('cell', 'presence'), ('entry', 'target')}
    for item in records:
        if not isinstance(item, list) or len(item) != 4:
            raise PackageError('each merge decision needs four columns')
        number, category, field, side = item
        if type(number) is not int or number != expected_id:
            raise PackageError('merge decision numbering must be consecutive from one')
        if not isinstance(category, str) or not isinstance(field, str):
            raise PackageError('merge decision category and field must be strings')
        if (category, field) not in allowed or side not in ('left', 'right'):
            raise PackageError('merge decision carries an unknown category, field or side')
        # Only the four disclosed columns travel with the piece; the private
        # workcell name stays at the issuing station.
        decoded.append((category, field, field, side))
        expected_id += 1
    provenance = MergeProvenance(station, revision, parent, left, right, tuple(decoded))
    if merge_block(provenance) != merge:
        raise PackageError('noncanonical or unexpected merge provenance fields')
    return provenance


def decode_package(raw: bytes) -> StationPackage:
    if not isinstance(raw, bytes):
        raise PackageError('original evidence must be bytes')
    try:
        envelope = json.loads(raw)
        body = envelope['body']
        fmt = body['format']
        if type(fmt) is not int or fmt not in (1, 2):
            raise PackageError('unsupported evidence format')
        if ('merge' in body) != (fmt == 2):
            raise PackageError('merge provenance must accompany format two and only format two')
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
        merge = _merge_provenance(body, identity) if fmt == 2 else None
        artifact = StationArtifact(identity, entries, merge)
        if encode_evidence(artifact) != raw:
            raise PackageError('noncanonical or unexpected evidence fields')
        return StationPackage(artifact, digest, raw)
    except PackageError:
        raise
    except (KeyError, TypeError, ValueError, ZeroDivisionError, RecursionError) as error:
        raise PackageError('malformed station evidence') from error
