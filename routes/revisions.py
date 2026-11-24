import json
import sqlite3

from routes.merger import MergeProvenance
from routes.model import RouteAction, RouteDraft, RouteRevision
from routes.validation import validate_route
from storage import canonical_json, initialize_tables, write_transaction


class RevisionNotFound(LookupError):
    pass


class RevisionStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        initialize_tables(self.db, '''
            CREATE TABLE IF NOT EXISTS route_revisions (
                revision_id INTEGER PRIMARY KEY,
                station TEXT NOT NULL,
                parent_revision INTEGER REFERENCES route_revisions(revision_id),
                route BLOB NOT NULL
            )
        ''', '''
            CREATE TABLE IF NOT EXISTS route_merges (
                revision_id INTEGER PRIMARY KEY REFERENCES route_revisions(revision_id),
                station TEXT NOT NULL,
                parent_revision INTEGER NOT NULL,
                left_revision INTEGER NOT NULL,
                right_revision INTEGER NOT NULL,
                decisions BLOB NOT NULL
            )
        ''')

    def _insert(self, draft: RouteDraft, parent_revision: int | None) -> int:
        payload = canonical_json({
            'entries': dict(draft.entries),
            'steps': {
                name: [[action.kind, str(action.probability), action.minutes, action.target] for action in actions]
                for name, actions in draft.steps.items()
            },
        })
        cursor = self.db.execute(
            'INSERT INTO route_revisions(station, parent_revision, route) VALUES (?, ?, ?)',
            (draft.station, parent_revision, payload),
        )
        return cursor.lastrowid

    def append(self, draft: RouteDraft, parent_revision: int | None = None) -> int:
        draft = validate_route(draft)
        with write_transaction(self.db):
            if parent_revision is not None:
                self._row(draft.station, parent_revision)
            revision_id = self._insert(draft, parent_revision)
        return revision_id

    def append_merge(self, draft: RouteDraft, provenance: MergeProvenance) -> int:
        """Commit the merged route and its provenance in one transaction."""
        draft = validate_route(draft)
        decisions = canonical_json([list(record) for record in provenance.decisions])
        with write_transaction(self.db):
            revision_id = self._insert(draft, provenance.parent)
            stored = MergeProvenance(
                provenance.station, revision_id, provenance.parent,
                provenance.left, provenance.right, provenance.decisions,
            )
            self.db.execute(
                '''INSERT INTO route_merges(revision_id, station, parent_revision,
                                            left_revision, right_revision, decisions)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (revision_id, stored.station, stored.parent,
                 stored.left, stored.right, decisions),
            )
        return revision_id

    def merge_provenance(self, station: str, revision_id: int) -> MergeProvenance | None:
        row = self.db.execute(
            '''SELECT parent_revision, left_revision, right_revision, decisions
               FROM route_merges WHERE station = ? AND revision_id = ?''',
            (station, revision_id),
        ).fetchone()
        if row is None:
            return None
        parent, left, right, decisions = row
        records = tuple(tuple(record) for record in json.loads(decisions))
        return MergeProvenance(station, revision_id, parent, left, right, records)

    def _row(self, station: str, revision_id: int):
        if not isinstance(station, str) or type(revision_id) is not int or not 0 < revision_id < 2**63:
            raise RevisionNotFound((station, revision_id))
        row = self.db.execute(
            'SELECT parent_revision, route FROM route_revisions WHERE station = ? AND revision_id = ?',
            (station, revision_id),
        ).fetchone()
        if row is None:
            raise RevisionNotFound((station, revision_id))
        return row

    def get(self, station: str, revision_id: int) -> RouteRevision:
        parent, payload = self._row(station, revision_id)
        record = json.loads(payload)
        steps = {name: tuple(RouteAction(*action) for action in actions) for name, actions in record['steps'].items()}
        draft = validate_route(RouteDraft(station, record['entries'], steps))
        return RouteRevision(revision_id, parent, draft)
