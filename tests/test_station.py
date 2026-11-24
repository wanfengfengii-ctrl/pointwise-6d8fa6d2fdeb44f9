import unittest
from fractions import Fraction as F

from exchange.package import decode_package
from routes.intake import register_route
from routes.model import RouteAction, RouteDraft
from station.artifact import ExitDestination
from station.issue import FreezeRejected, freeze_station
from tests import Workbench, single


class StationTests(unittest.TestCase):
    def setUp(self):
        self.workbench = Workbench(self)

    def test_exit_time_and_destination(self):
        draft = RouteDraft('A', {'intake': 'private_source', 'rerun': 'private_finish'}, {
            'private_source': (RouteAction('step', '1/2', 1, 'private_finish'), RouteAction('handoff', '1/2', 0, 'peer')),
            'private_finish': (RouteAction('release', 1, 1),),
        })
        revision, raw = self.workbench.publish(draft)
        self.workbench.disconnect_sources()
        package = decode_package(raw)
        self.assertEqual((package.artifact.identity.station, package.artifact.identity.revision), ('A', revision))
        entries = {entry.name: entry for entry in package.artifact.entries}
        outputs = dict(entries['intake'].exits)
        self.assertEqual(outputs[ExitDestination('release')].coefficient_prefix(2), (0, 0, F(1, 2)))
        self.assertEqual(outputs[ExitDestination('handoff', 'peer')].coefficient_prefix(2), (F(1, 2), 0, 0))
        self.assertEqual(dict(entries['rerun'].exits)[ExitDestination('release')].coefficient_prefix(1), (0, 1))
        self.assertEqual(entries['intake'].unexited, 0)
        self.assertNotIn(b'private_source', raw)
        self.assertNotIn(b'private_finish', raw)

    def test_joint_marginals(self):
        packages = []
        for release_minute in (0, 1):
            _, raw = self.workbench.publish(single('A', RouteAction('release', '1/2', release_minute),
                                                   RouteAction('handoff', '1/2', 1 - release_minute, 'intake')))
            packages.append(raw)
        self.workbench.disconnect_sources()
        immediate_release = []
        for raw in packages:
            artifact = decode_package(raw).artifact
            outputs = dict(artifact.entries[0].exits)
            released, sent = outputs[ExitDestination('release')], outputs[ExitDestination('handoff', 'intake')]
            self.assertEqual((released.total_mass(), sent.total_mass()), (F(1, 2), F(1, 2)))
            self.assertEqual(tuple(map(sum, zip(released.coefficient_prefix(1), sent.coefficient_prefix(1)))), (F(1, 2), F(1, 2)))
            immediate_release.append(released.coefficient_prefix(0)[0])
        self.assertEqual(immediate_release, [F(1, 2), 0])

    def test_local_retry_preserves_time_tail(self):
        _, raw = self.workbench.publish(single('A', RouteAction('step', '1/4', 0, 'private_cell'),
                                               RouteAction('step', '1/4', 1, 'private_cell'),
                                               RouteAction('release', '1/4', 1), RouteAction('hold', '1/4', 0)))
        entry = decode_package(raw).artifact.entries[0]
        law = dict(entry.exits)[ExitDestination('release')]
        self.assertEqual(law.coefficient_prefix(3), (0, F(1, 3), F(1, 9), F(1, 27)))
        self.assertEqual(law.total_mass(), F(1, 2))
        self.assertEqual(entry.unexited, F(1, 2))

    def test_non_exit_mass(self):
        draft = RouteDraft('A', {'intake': 'work', 'stalled': 'closed'}, {
            'work': (RouteAction('release', '1/3', 0), RouteAction('step', '2/3', 0, 'closed')),
            'closed': (RouteAction('step', 1, 1, 'closed'),),
        })
        _, raw = self.workbench.publish(draft)
        first, stalled = decode_package(raw).artifact.entries
        self.assertEqual(first.unexited, F(2, 3))
        self.assertEqual(dict(first.exits)[ExitDestination('release')].total_mass(), F(1, 3))
        self.assertEqual((stalled.exits, stalled.unexited), ((), 1))

    def test_issue_old_revision_and_reopen_artifact(self):
        revisions, evidence = self.workbench.site('A')
        old = register_route(revisions, single('A', RouteAction('release', 1, 1)))
        new = register_route(revisions, single('A', RouteAction('release', 1, 0)), old)
        raw = freeze_station(revisions, evidence, 'A', old)
        new_raw = freeze_station(revisions, evidence, 'A', new)
        self.assertNotEqual(raw, new_raw)
        old_piece = decode_package(raw).artifact
        self.assertEqual(dict(old_piece.entries[0].exits)[ExitDestination('release')].coefficient_prefix(1), (0, 1))
        self.workbench.close_site('A')
        revisions, evidence = self.workbench.site('A')
        self.assertEqual(evidence.get(old_piece.identity.piece_id), raw)
        self.assertEqual(freeze_station(revisions, evidence, 'A', old), raw)

    def test_missing_revision(self):
        revisions, evidence = self.workbench.site('A')
        with self.assertRaises(FreezeRejected):
            freeze_station(revisions, evidence, 'A', 77)
        self.assertEqual(evidence.db.execute('SELECT count(*) FROM station_evidence').fetchone()[0], 0)
