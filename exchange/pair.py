import json
import sqlite3
from dataclasses import asdict, dataclass

from exchange.connection import PortConnection, UnresolvedPortError, resolve_connections
from exchange.package import PackageError
from exchange.receipt import ReceiptStore, ReceivedPiece
from storage import canonical_json, initialize_tables, write_transaction


class PairingRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StationPair:
    pair_id: int
    pieces: tuple[ReceivedPiece, ReceivedPiece]
    connections: tuple[PortConnection, ...]


class PairStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.receipts = ReceiptStore(db)

    def initialize(self) -> None:
        initialize_tables(self.db, '''
            CREATE TABLE IF NOT EXISTS station_pairs (
                pair_id INTEGER PRIMARY KEY,
                first_receipt INTEGER NOT NULL REFERENCES received_pieces(receipt_id),
                second_receipt INTEGER NOT NULL REFERENCES received_pieces(receipt_id),
                connections BLOB NOT NULL,
                CHECK(first_receipt <> second_receipt)
            )
        ''')

    def persist(self, first_receipt_id: int, second_receipt_id: int) -> StationPair:
        with write_transaction(self.db):
            first = self.receipts.get(first_receipt_id)
            second = self.receipts.get(second_receipt_id)
            connections = resolve_connections(first, second)
            cursor = self.db.execute(
                'INSERT INTO station_pairs(first_receipt, second_receipt, connections) VALUES (?, ?, ?)',
                (first.receipt_id, second.receipt_id, canonical_json([asdict(link) for link in connections])),
            )
            pair_id = cursor.lastrowid
        return StationPair(pair_id, (first, second), connections)

    def get(self, pair_id: int) -> StationPair:
        if type(pair_id) is not int or not 0 < pair_id < 2**63:
            raise KeyError(pair_id)
        row = self.db.execute(
            'SELECT first_receipt, second_receipt, connections FROM station_pairs WHERE pair_id = ?', (pair_id,),
        ).fetchone()
        if row is None:
            raise KeyError(pair_id)
        first, second, connections = row
        pieces = (self.receipts.get(first), self.receipts.get(second))
        links = tuple(PortConnection(**link) for link in json.loads(connections))
        return StationPair(pair_id, pieces, links)


def pair_received(store: PairStore, first_receipt_id: int, second_receipt_id: int) -> StationPair:
    try:
        return store.persist(first_receipt_id, second_receipt_id)
    except KeyError as error:
        raise PairingRejected('specified receipt was not found') from error
    except (UnresolvedPortError, PackageError) as error:
        raise PairingRejected(str(error)) from error
