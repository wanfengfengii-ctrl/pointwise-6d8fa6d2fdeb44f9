from routes.revisions import RevisionNotFound, RevisionStore
from station.artifact import ArtifactIdentity, StationArtifact
from station.evidence_store import EvidenceStore
from station.first_exit import derive_first_exits


class FreezeRejected(ValueError):
    pass


def freeze_station(revisions: RevisionStore, evidence: EvidenceStore, station: str, revision_id: int) -> bytes:
    try:
        revision = revisions.get(station, revision_id)
    except RevisionNotFound as error:
        raise FreezeRejected('specified station revision was not found') from error
    identity = ArtifactIdentity(station, revision_id)
    issued = evidence.get(identity.piece_id)
    if issued is not None:
        return issued
    merge = revisions.merge_provenance(station, revision_id)
    artifact = StationArtifact(identity, derive_first_exits(revision), merge)
    return evidence.save(artifact)
