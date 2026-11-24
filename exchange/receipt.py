import sqlite3
from dataclasses import dataclass

from exchange.package import StationPackage, decode_package
from storage import initialize_tables, write_transaction


class ReceiptConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReceivedPiece:
    receipt_id: int
    package: StationPackage


class ReceiptStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        initialize_tables(self.db, '''
            CREATE TABLE IF NOT EXISTS received_pieces (
                receipt_id INTEGER PRIMARY KEY,
                piece_id TEXT NOT NULL UNIQUE,
                original BLOB NOT NULL
            )
        ''')

    def record(self, package: StationPackage) -> ReceivedPiece:
        piece_id = package.artifact.identity.piece_id
        with write_transaction(self.db):
            existing = self.db.execute(
                'SELECT receipt_id, original FROM received_pieces WHERE piece_id = ?', (piece_id,),
            ).fetchone()
            if existing is not None:
                receipt_id, original = existing
                if original != package.original:
                    raise ReceiptConflictError('received identity already binds a different original')
            else:
                cursor = self.db.execute(
                    'INSERT INTO received_pieces(piece_id, original) VALUES (?, ?)', (piece_id, package.original),
                )
                receipt_id = cursor.lastrowid
        return ReceivedPiece(receipt_id, package)

    def get(self, receipt_id: int) -> ReceivedPiece:
        if type(receipt_id) is not int or not 0 < receipt_id < 2**63:
            raise KeyError(receipt_id)
        row = self.db.execute('SELECT original FROM received_pieces WHERE receipt_id = ?', (receipt_id,)).fetchone()
        if row is None:
            raise KeyError(receipt_id)
        return ReceivedPiece(receipt_id, decode_package(row[0]))
