import unittest

from routes.intake import RegistrationRejected, register_route
from routes.model import RouteAction, RouteDraft
from routes.revisions import RevisionNotFound
from tests import Workbench, single


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.workbench = Workbench(self)
        self.revisions, _ = self.workbench.site('A')
        self.original = single('A', RouteAction('release', 1, 1))
        self.old_id = register_route(self.revisions, self.original)

    def test_revision_keeps_old_route(self):
        new = single('A', RouteAction('release', 1, 0))
        new_id = register_route(self.revisions, new, self.old_id)
        self.assertNotEqual(self.old_id, new_id)
        old = self.revisions.get('A', self.old_id)
        corrected = self.revisions.get('A', new_id)
        self.assertIsNone(old.parent_revision)
        self.assertEqual(corrected.parent_revision, self.old_id)
        self.assertEqual(old.draft.steps['private_cell'][0].minutes, 1)
        self.assertEqual(corrected.draft.steps['private_cell'][0].minutes, 0)

    def test_invalid_probability_sum(self):
        invalid = single('A', RouteAction('release', '3/4', 0), RouteAction('hold', '3/4', 0))
        with self.assertRaisesRegex(RegistrationRejected, 'probability sum'):
            register_route(self.revisions, invalid, self.old_id)
        self.assertEqual(self.revisions.db.execute('SELECT count(*) FROM route_revisions').fetchone()[0], 1)
        self.assertEqual(self.revisions.get('A', self.old_id).draft, self.original)

    def test_reopen_explicit_revision_and_reinitialize_keeps_correction(self):
        new = single('A', RouteAction('release', 1, 0))
        new_id = register_route(self.revisions, new, self.old_id)
        self.workbench.close_site('A')
        reopened, _ = self.workbench.site('A')
        reopened.initialize()
        self.assertEqual(reopened.get('A', self.old_id).draft, self.original)
        self.assertEqual(reopened.get('A', new_id).draft, new)
        self.assertEqual(reopened.get('A', new_id).parent_revision, self.old_id)
        with self.assertRaises(RevisionNotFound):
            reopened.get('B', new_id)

    def test_invalid_duration(self):
        for minutes in (-1, 2, True, None):
            with self.subTest(minutes=minutes), self.assertRaisesRegex(RegistrationRejected, 'minutes'):
                register_route(self.revisions, single('A', RouteAction('release', 1, minutes)))

    def test_undefined_local_port(self):
        for draft in (
            RouteDraft('A', {'intake': 'missing'}, {'cell': (RouteAction('hold', 1, 0),)}),
            single('A', RouteAction('step', 1, 0, 'missing')),
        ):
            with self.subTest(draft=draft), self.assertRaises(RegistrationRejected):
                register_route(self.revisions, draft, self.old_id)

    def test_missing_or_other_station_parent(self):
        for draft, parent in ((self.original, 999), (single('B', RouteAction('hold', 1, 0)), self.old_id)):
            with self.subTest(station=draft.station, parent=parent), self.assertRaisesRegex(RegistrationRejected, 'parent'):
                register_route(self.revisions, draft, parent)
        self.assertEqual(self.revisions.db.execute('SELECT count(*) FROM route_revisions').fetchone()[0], 1)
