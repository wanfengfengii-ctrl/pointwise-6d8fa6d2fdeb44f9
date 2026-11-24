import sqlite3

from station.artifact import StationArtifact, encode_evidence
from storage import initialize_tables, write_transaction


class EvidenceStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        initialize_tables(self.db, '''
            CREATE TABLE IF NOT EXISTS station_evidence (
                piece_id TEXT PRIMARY KEY,
                station TEXT NOT NULL,
                revision INTEGER NOT NULL,
                original BLOB NOT NULL,
                UNIQUE(station, revision)
            )
        ''')

    def save(self, artifact: StationArtifact) -> bytes:
        identity = artifact.identity
        raw = encode_evidence(artifact)
        with write_transaction(self.db):
            existing = self.get(identity.piece_id)
            if existing is not None:
                if existing != raw:
                    raise ValueError('issued identity already binds different evidence')
                return existing
            self.db.execute(
                'INSERT INTO station_evidence(piece_id, station, revision, original) VALUES (?, ?, ?, ?)',
                (identity.piece_id, identity.station, identity.revision, raw),
            )
        return raw

    def get(self, piece_id: str) -> bytes | None:
        row = self.db.execute(
            'SELECT original FROM station_evidence WHERE piece_id = ?', (piece_id,),
        ).fetchone()
        return None if row is None else row[0]
