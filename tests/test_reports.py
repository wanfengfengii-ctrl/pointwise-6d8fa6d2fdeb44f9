import json
import sqlite3
import unittest
from base64 import b64decode
from fractions import Fraction as F

from demo import sample_routes
from reports.export import export_report
from reports.history import ReportNotFound, read_report
from reports.issue import IssuanceFailed
from routes.model import RouteAction
from tests import Workbench, single


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.workbench = Workbench(self)

    def _ordinary(self):
        _, a = self.workbench.publish(single('A', RouteAction('release', 1, 1)))
        _, b = self.workbench.publish(single('B', RouteAction('hold', 1, 0)))
        return self.workbench.pair(a, b)

    def test_failed_receipt_rolls_back(self):
        pair = self._ordinary()
        self.workbench.center.execute('''
            CREATE TRIGGER stop_receipt BEFORE INSERT ON report_receipts
            BEGIN
                SELECT RAISE(ABORT, 'receipt storage unavailable');
            END
        ''')
        with self.assertRaisesRegex(IssuanceFailed, 'receipt storage unavailable'):
            self.workbench.sign(pair.pair_id, 1)
        self.assertFalse(self.workbench.center.in_transaction)
        self.workbench.reopen_center()
        with self.assertRaises(ReportNotFound):
            read_report(self.workbench.reports, 1)
        for table in ('final_reports', 'report_receipts'):
            self.assertEqual(self.workbench.center.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        self.workbench.center.execute('DROP TRIGGER stop_receipt')
        report = self.workbench.sign(pair.pair_id, 1)
        self.assertEqual(read_report(self.workbench.reports, report.report_id), report)

    def test_commit_success(self):
        pair = self._ordinary()
        issued = self.workbench.sign(pair.pair_id, 1)
        self.workbench.reopen_center()
        loaded = read_report(self.workbench.reports, issued.report_id)
        self.assertEqual(loaded, issued)
        self.assertEqual((loaded.result.by_deadline, loaded.result.eventual), (1, 1))
        self.assertEqual(loaded.result.pair_id, pair.pair_id)
        self.assertEqual(loaded.originals, tuple(piece.package.original for piece in pair.pieces))
        record = json.loads(export_report(self.workbench.reports, issued.report_id))
        self.assertEqual(record['receipt']['receipt_code'], issued.receipt.receipt_code)
        self.assertEqual(record['result']['by_deadline'], '1')
        self.assertEqual(tuple(b64decode(raw) for raw in record['originals']), loaded.originals)

    def test_old_result_after_correction(self):
        original, correction, peer = sample_routes()
        parent, old_a = self.workbench.publish(original)
        _, b = self.workbench.publish(peer)
        old_pair = self.workbench.pair(old_a, b)
        old = self.workbench.sign(old_pair.pair_id, 0)
        old_export = export_report(self.workbench.reports, old.report_id)
        self.workbench.close_site('A')
        new_revision, new_a = self.workbench.publish(correction, parent)
        new_pair = self.workbench.pair(new_a, b)
        self.workbench.disconnect_sources()
        latest = self.workbench.sign(new_pair.pair_id, 0)
        self.assertEqual(latest.result.sources[0].revision, new_revision)
        self.assertNotEqual(latest.result.sources[0].digest, old.result.sources[0].digest)
        self.assertEqual(latest.result.by_deadline, F(2, 3))
        self.workbench.reopen_center()

        blocked = []

        def archive_tables_only(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table not in ('final_reports', 'report_receipts'):
                blocked.append(table)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.workbench.center.set_authorizer(archive_tables_only)
        recovered = read_report(self.workbench.reports, old.report_id)
        self.assertEqual(recovered, old)
        self.assertEqual(recovered.result.by_deadline, F(2, 7))
        self.assertEqual(recovered.result.sources[0].revision, parent)
        self.assertEqual(recovered.originals, (old_a, b))
        self.assertEqual(export_report(self.workbench.reports, old.report_id), old_export)
        self.assertEqual(blocked, [])

    def test_not_found(self):
        for report_id in (1, -1, True, None):
            with self.subTest(report_id=report_id):
                with self.assertRaises(ReportNotFound):
                    read_report(self.workbench.reports, report_id)
                with self.assertRaises(ReportNotFound):
                    export_report(self.workbench.reports, report_id)
