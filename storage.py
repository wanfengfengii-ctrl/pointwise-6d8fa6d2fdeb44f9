import json
import sqlite3
from contextlib import contextmanager


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


@contextmanager
def write_transaction(db: sqlite3.Connection):
    if db.in_transaction:
        raise sqlite3.OperationalError('finish the caller transaction before writing')
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def initialize_tables(db: sqlite3.Connection, *statements: str) -> None:
    if db.in_transaction:
        raise sqlite3.OperationalError('finish the caller transaction before initialization')
    db.execute('PRAGMA foreign_keys = ON')
    with write_transaction(db):
        for statement in statements:
            db.execute(statement)
