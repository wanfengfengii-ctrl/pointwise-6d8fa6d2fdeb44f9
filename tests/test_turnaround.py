import unittest
from fractions import Fraction as F

from demo import sample_routes
from routes.model import RouteAction, RouteDraft
from review.request import RequestRejected
from tests import Workbench, single


class TurnaroundTests(unittest.TestCase):
    def setUp(self):
        self.workbench = Workbench(self)

    def _pair(self, a, b):
        _, left = self.workbench.publish(a)
        _, right = self.workbench.publish(b)
        return self.workbench.pair(left, right)

    def test_exact_deadline(self):
        pair = self._pair(single('A', RouteAction('handoff', 1, 0, 'intake')), single('B', RouteAction('release', 1, 1)))
        self.workbench.disconnect_sources()
        for deadline, expected in ((0, 0), (1, 1)):
            report = self.workbench.sign(pair.pair_id, deadline)
            self.assertEqual((report.result.by_deadline, report.result.eventual), (expected, 1))
            self.assertEqual(report.receipt.report_id, report.report_id)

    def test_invalid_request(self):
        pair = self._pair(single('A', RouteAction('release', 1, 0)), single('B', RouteAction('hold', 1, 0)))
        for pair_id, deadline, station, entry in (
            (pair.pair_id, -1, 'A', 'intake'), (pair.pair_id, True, 'A', 'intake'),
            (pair.pair_id, None, 'A', 'intake'), (pair.pair_id, F(1), 'A', 'intake'),
            (pair.pair_id, 0, 'A', 'missing'), (pair.pair_id, 0, 'C', 'intake'),
            (pair.pair_id, 0, 'A', ''), (999, 0, 'A', 'intake'),
        ):
            with self.subTest(pair=pair_id, deadline=deadline, station=station, entry=entry), self.assertRaises(RequestRejected):
                self.workbench.sign(pair_id, deadline, station, entry)
        for table in ('final_reports', 'report_receipts'):
            self.assertEqual(self.workbench.center.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_return_loop(self):
        original, _, peer = sample_routes()
        pair = self._pair(original, peer)
        self.workbench.disconnect_sources()
        self.workbench.reopen_center()
        first = self.workbench.sign(pair.pair_id, 0)
        second = self.workbench.sign(pair.pair_id, 1)
        self.assertEqual((first.result.by_deadline, second.result.by_deadline), (F(2, 7), F(22, 49)))
        self.assertEqual((first.result.eventual, second.result.eventual), (F(2, 3), F(2, 3)))
        self.assertEqual(1 - second.result.eventual, F(1, 3))
        self.assertEqual(first.originals, tuple(piece.package.original for piece in pair.pieces))
        self.assertEqual(second.result.sources, first.result.sources)

    def test_offline_selected_pair(self):
        original, correction, peer = sample_routes()
        parent, old = self.workbench.publish(original)
        corrected_revision, new = self.workbench.publish(correction, parent)
        _, b = self.workbench.publish(peer)
        old_pair, new_pair = [self.workbench.pair(a, b) for a in (old, new)]
        self.workbench.disconnect_sources()
        self.workbench.reopen_center()
        first = self.workbench.sign(old_pair.pair_id, 0)
        revised = self.workbench.sign(new_pair.pair_id, 0)
        self.assertEqual((first.result.by_deadline, revised.result.by_deadline), (F(2, 7), F(2, 3)))
        self.assertEqual((first.result.sources[0].revision, revised.result.sources[0].revision), (parent, corrected_revision))
        self.assertEqual(first.result.sources[1], revised.result.sources[1])
        self.assertEqual((first.originals, revised.originals), ((old, b), (new, b)))

    def test_joint_marginals_after_pairing(self):
        _, hold = self.workbench.publish(single('B', RouteAction('hold', 1, 0)))
        pairs = []
        for release_minute in (0, 1):
            _, raw = self.workbench.publish(single('A', RouteAction('release', '1/2', release_minute),
                                                   RouteAction('handoff', '1/2', 1 - release_minute, 'intake')))
            pairs.append(self.workbench.pair(raw, hold))
        self.workbench.disconnect_sources()
        first, second = [self.workbench.sign(pair.pair_id, 0).result for pair in pairs]
        self.assertEqual((first.by_deadline, second.by_deadline), (F(1, 2), 0))
        self.assertEqual((first.eventual, second.eventual), (F(1, 2), F(1, 2)))

    def _closed_loop(self, minutes):
        a = RouteDraft('A', {'intake': 'start', 'loop': 'revisit'}, {
            'start': (RouteAction('release', '1/2', 0), RouteAction('handoff', '1/2', 0, 'loop')),
            'revisit': (RouteAction('handoff', 1, 0, 'loop'),),
        })
        b = single('B', RouteAction('handoff', 1, minutes, 'loop'), entry='loop')
        pair = self._pair(a, b)
        self.workbench.disconnect_sources()
        return self.workbench.sign(pair.pair_id, 0)

    def test_closed_instant_loop(self):
        report = self._closed_loop(0)
        self.assertEqual((report.result.by_deadline, report.result.eventual), (F(1, 2), F(1, 2)))
        self.assertEqual(report.receipt.report_id, report.report_id)

    def test_positive_time_closed(self):
        report = self._closed_loop(1)
        self.assertEqual((report.result.by_deadline, report.result.eventual), (F(1, 2), F(1, 2)))
        self.assertEqual(report.receipt.report_id, report.report_id)

    def test_no_release(self):
        pair = self._pair(single('A', RouteAction('handoff', 1, 0, 'intake')),
                          single('B', RouteAction('handoff', 1, 0, 'intake')))
        self.workbench.disconnect_sources()
        report = self.workbench.sign(pair.pair_id, 3)
        self.assertEqual((report.result.by_deadline, report.result.eventual), (0, 0))
        self.assertEqual(report.receipt.report_id, report.report_id)
