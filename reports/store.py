import json
import sqlite3
from dataclasses import dataclass

from review.conclusion import TurnaroundResult
from storage import canonical_json, initialize_tables, write_transaction


@dataclass(frozen=True, slots=True)
class ReportReceipt:
    report_id: int
    receipt_code: str


@dataclass(frozen=True, slots=True)
class FinalReport:
    report_id: int
    result: TurnaroundResult
    originals: tuple[bytes, bytes]
    receipt: ReportReceipt


class ReportStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self) -> None:
        initialize_tables(
            self.db,
            '''
            CREATE TABLE IF NOT EXISTS final_reports (
                report_id INTEGER PRIMARY KEY,
                conclusion BLOB NOT NULL,
                first_original BLOB NOT NULL,
                second_original BLOB NOT NULL
            )
            ''',
            '''
            CREATE TABLE IF NOT EXISTS report_receipts (
                report_id INTEGER PRIMARY KEY REFERENCES final_reports(report_id),
                receipt_code TEXT NOT NULL UNIQUE
            )
            ''',
        )

    def commit(self, result: TurnaroundResult, originals: tuple[bytes, bytes]) -> FinalReport:
        record = canonical_json(result.to_record())
        with write_transaction(self.db):
            cursor = self.db.execute(
                'INSERT INTO final_reports(conclusion, first_original, second_original) VALUES (?, ?, ?)',
                (record, *originals),
            )
            report_id = cursor.lastrowid
            receipt = ReportReceipt(report_id, f'R-{report_id}')
            self.db.execute(
                'INSERT INTO report_receipts(report_id, receipt_code) VALUES (?, ?)',
                (receipt.report_id, receipt.receipt_code),
            )
        return FinalReport(report_id, result, originals, receipt)

    def load_finalized(self, report_id: int) -> FinalReport | None:
        if type(report_id) is not int or not 0 < report_id < 2**63:
            return None
        row = self.db.execute('''
            SELECT conclusion, first_original, second_original, receipt_code
            FROM final_reports JOIN report_receipts USING(report_id)
            WHERE report_id = ?
        ''', (report_id,)).fetchone()
        if row is None:
            return None
        conclusion, first, second, code = row
        result = TurnaroundResult.from_record(json.loads(conclusion))
        return FinalReport(report_id, result, (first, second), ReportReceipt(report_id, code))
