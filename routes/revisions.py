import json
import sqlite3

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
        ''')

    def append(self, draft: RouteDraft, parent_revision: int | None = None) -> int:
        draft = validate_route(draft)
        payload = canonical_json({
            'entries': dict(draft.entries),
            'steps': {
                name: [[action.kind, str(action.probability), action.minutes, action.target] for action in actions]
                for name, actions in draft.steps.items()
            },
        })
        with write_transaction(self.db):
            if parent_revision is not None:
                self._row(draft.station, parent_revision)
            cursor = self.db.execute(
                'INSERT INTO route_revisions(station, parent_revision, route) VALUES (?, ?, ?)',
                (draft.station, parent_revision, payload),
            )
            revision_id = cursor.lastrowid
        return revision_id

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
