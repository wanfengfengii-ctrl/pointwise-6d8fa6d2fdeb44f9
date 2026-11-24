import json
import unittest

from exchange.connection import PortConnection
from exchange.pair import PairingRejected, pair_received
from exchange.receive import ReceiptRejected
from routes.model import RouteAction
from station.artifact import evidence_digest
from storage import canonical_json
from tests import Workbench, single


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.workbench = Workbench(self)

    def test_corrupt_piece(self):
        _, raw = self.workbench.publish(single('A', RouteAction('release', 1, 0)))
        bad_digest = json.loads(raw)
        bad_digest['digest'] = '0' * 64
        wrong_identity = json.loads(raw)
        wrong_identity['body']['station'] = 'B'
        wrong_identity['digest'] = evidence_digest(wrong_identity['body'])
        bad_mass = json.loads(raw)
        bad_mass['body']['entries'][0]['unexited'] = '1/2'
        bad_mass['digest'] = evidence_digest(bad_mass['body'])
        bad_time_law = json.loads(raw)
        bad_time_law['body']['entries'][0]['exits'][0][2] = [['poly', ['2'], ['1', '-2']]]
        bad_time_law['digest'] = evidence_digest(bad_time_law['body'])
        cases = [raw[:-5], *(canonical_json(item) for item in (bad_digest, wrong_identity, bad_mass, bad_time_law))]
        for damaged in cases:
            with self.subTest(damaged=damaged), self.assertRaises(ReceiptRejected):
                self.workbench.receive(damaged)
        self.assertEqual(self.workbench.center.execute('SELECT count(*) FROM received_pieces').fetchone()[0], 0)

    def test_same_piece_retry(self):
        _, raw = self.workbench.publish(single('A', RouteAction('release', 1, 0)))
        original = self.workbench.receive(raw)
        self.workbench.reopen_center()
        duplicate = self.workbench.receive(raw)
        self.assertEqual(original.receipt_id, duplicate.receipt_id)
        self.assertEqual(duplicate.package.original, raw)
        self.assertEqual(self.workbench.center.execute('SELECT count(*) FROM received_pieces').fetchone()[0], 1)

    def test_identity_conflict(self):
        _, raw = self.workbench.publish(single('A', RouteAction('release', 1, 0)))
        original = self.workbench.receive(raw)
        conflict = json.loads(raw)
        conflict['body']['entries'][0]['exits'][0][2][0][1] = ['0', '1']
        conflict['digest'] = evidence_digest(conflict['body'])
        with self.assertRaisesRegex(ReceiptRejected, 'different original'):
            self.workbench.receive(canonical_json(conflict))
        self.assertEqual(self.workbench.receipts.get(original.receipt_id).package.original, raw)
        self.assertEqual(self.workbench.receive(raw).receipt_id, original.receipt_id)

    def test_unresolved_port(self):
        _, raw_a = self.workbench.publish(single('A', RouteAction('handoff', 1, 0, 'reinspect'), entry='reinspect'))
        _, raw_b = self.workbench.publish(single('B', RouteAction('hold', 1, 0)))
        left, right = [self.workbench.receive(raw) for raw in (raw_a, raw_b)]
        with self.assertRaisesRegex(PairingRejected, "paired station 'B'"):
            pair_received(self.workbench.pairs, left.receipt_id, right.receipt_id)
        self.assertEqual(self.workbench.receipts.get(left.receipt_id).package.original, raw_a)
        self.assertEqual(self.workbench.receipts.get(right.receipt_id).package.original, raw_b)
        self.assertEqual(self.workbench.center.execute('SELECT count(*) FROM station_pairs').fetchone()[0], 0)
        self.assertEqual(self.workbench.center.execute('SELECT count(*) FROM final_reports').fetchone()[0], 0)

    def test_directed_match_with_same_name(self):
        _, raw_a = self.workbench.publish(single('A', RouteAction('handoff', 1, 0, 'intake')))
        _, raw_b = self.workbench.publish(single('B', RouteAction('handoff', '1/2', 0, 'intake'), RouteAction('hold', '1/2', 0)))
        pair = self.workbench.pair(raw_a, raw_b)
        left, right = pair.pieces
        self.workbench.reopen_center()
        loaded = self.workbench.pairs.get(pair.pair_id)
        self.assertEqual(loaded.connections, (
            PortConnection(left.receipt_id, 'intake', right.receipt_id, 'intake'),
            PortConnection(right.receipt_id, 'intake', left.receipt_id, 'intake'),
        ))

    def test_missing_piece_and_same_station_pair(self):
        _, raw = self.workbench.publish(single('A', RouteAction('hold', 1, 0)))
        _, alternate = self.workbench.publish(single('A', RouteAction('release', 1, 0)))
        first = self.workbench.receive(raw)
        second = self.workbench.receive(alternate)
        for other in (999, first.receipt_id, second.receipt_id):
            with self.subTest(other=other), self.assertRaises(PairingRejected):
                pair_received(self.workbench.pairs, first.receipt_id, other)
        self.assertEqual(self.workbench.center.execute('SELECT count(*) FROM station_pairs').fetchone()[0], 0)
