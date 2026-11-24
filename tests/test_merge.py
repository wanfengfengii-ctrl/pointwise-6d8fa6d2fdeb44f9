import json
import unittest
from base64 import b64decode
from fractions import Fraction as F

from exchange.package import decode_package
from exchange.receive import ReceiptRejected, receive_station_piece
from reports.export import export_report
from reports.history import read_report
from review.request import ReportRequest, report_turnaround
from routes.intake import RegistrationRejected, merge_routes, register_route
from routes.model import RouteAction, RouteDraft
from samples.merge_case import dangling_drafts, parallel_drafts
from station.artifact import evidence_digest
from station.issue import freeze_station
from storage import canonical_json
from tests import Workbench


class MergeRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.workbench = Workbench(self)
        self.revisions, self.evidence = self.workbench.site('A')
        parent, left, right, peer = parallel_drafts()
        self.parent_draft, self.left_draft, self.right_draft = parent, left, right
        self.parent = register_route(self.revisions, parent)
        self.left = register_route(self.revisions, left, self.parent)
        self.right = register_route(self.revisions, right, self.parent)

    def _merge(self, *, left_renames=None, right_renames=None, decisions=None):
        return merge_routes(
            self.revisions, 'A', self.parent, self.left, self.right,
            left_renames=left_renames or {}, right_renames=right_renames or {},
            decisions=decisions or {},
        )

    def test_rename_row_merges_without_decisions(self):
        revision = self._merge(left_renames={'scan': 'verify'})
        merged = self.revisions.get('A', revision)
        self.assertEqual(merged.parent_revision, self.parent)
        self.assertEqual(dict(merged.draft.entries), {'intake': 'verify', 'backup': 'scan'})
        self.assertEqual(merged.draft.steps['verify'][0].minutes, 1)
        self.assertEqual(merged.draft.steps['scan'][0].kind, 'hold')
        provenance = self.revisions.merge_provenance('A', revision)
        self.assertEqual((provenance.parent, provenance.left, provenance.right),
                         (self.parent, self.left, self.right))
        self.assertEqual(provenance.decisions, ())

    def test_action_conflict_requires_exactly_one_decision(self):
        revision = self._merge(decisions={('cell', 'scan', 'actions'): 'right'})
        merged = self.revisions.get('A', revision)
        self.assertEqual(merged.draft.steps['scan'][0].minutes, 1)
        self.assertEqual(merged.draft.steps['verify'][0].minutes, 0)
        block = json.loads(freeze_station(self.revisions, self.evidence, 'A', revision))['body']['merge']
        self.assertEqual(block['decisions'], [[1, 'cell', 'actions', 'right']])

    def test_missing_extra_and_illegal_decisions_are_rejected(self):
        cases = [
            {},
            {('cell', 'scan', 'actions'): 'up'},
            {('cell', 'scan', 'actions'): 'right', ('cell', 'scan', 'name'): 'left'},
            {('cell', 'ghost', 'actions'): 'right'},
            {('entry', 'intake', 'target'): 'left'},
        ]
        for decisions in cases:
            with self.subTest(decisions=decisions):
                with self.assertRaises(RegistrationRejected):
                    self._merge(decisions=decisions)
        self.assertEqual(self.revisions.db.execute('SELECT count(*) FROM route_revisions').fetchone()[0], 3)
        self.assertEqual(self.revisions.db.execute('SELECT count(*) FROM route_merges').fetchone()[0], 0)

    def test_invalid_rename_declarations_are_rejected(self):
        cases = [
            ({'nope': 'verify'}, {}),
            ({'scan': 'nope'}, {}),
            ({'scan': 'scan'}, {}),
            ({}, {'scan': 7}),
        ]
        for left_renames, right_renames in cases:
            with self.subTest(left_renames=left_renames, right_renames=right_renames):
                with self.assertRaises(RegistrationRejected):
                    self._merge(left_renames=left_renames, right_renames=right_renames)

    def test_children_must_be_distinct_and_direct_descendants(self):
        with self.assertRaisesRegex(RegistrationRejected, 'distinct'):
            merge_routes(self.revisions, 'A', self.parent, self.left, self.left,
                         left_renames={}, right_renames={}, decisions={})
        grandchild = register_route(self.revisions, self.right_draft, self.left)
        with self.assertRaisesRegex(RegistrationRejected, 'directly follow'):
            merge_routes(self.revisions, 'A', self.left, self.right, grandchild,
                         left_renames={}, right_renames={}, decisions={})

    def test_duplicate_final_name_is_rejected(self):
        hold = (RouteAction('hold', 1, 0),)
        parent = RouteDraft('A', {'intake': 'a'}, {'a': hold, 'b': hold})
        left = RouteDraft('A', {'intake': 'shared'}, {'shared': hold, 'b': hold})
        right = RouteDraft('A', {'intake': 'a'}, {'a': hold, 'shared': hold})
        pid = register_route(self.revisions, parent)
        lid = register_route(self.revisions, left, pid)
        rid = register_route(self.revisions, right, pid)
        with self.assertRaisesRegex(RegistrationRejected, 'duplicate workcell name'):
            merge_routes(self.revisions, 'A', pid, lid, rid,
                         left_renames={'a': 'shared'}, right_renames={'b': 'shared'}, decisions={})

    def test_dangling_route_is_rejected_without_leaving_rows(self):
        parent, left, right = dangling_drafts()
        pid = register_route(self.revisions, parent)
        lid = register_route(self.revisions, left, pid)
        rid = register_route(self.revisions, right, pid)
        before = self.revisions.db.execute('SELECT count(*) FROM route_revisions').fetchone()[0]
        for decisions in ({}, {('cell', 'scan', 'presence'): 'left'},
                          {('cell', 'scan', 'presence'): 'right'}):
            with self.subTest(decisions=decisions):
                with self.assertRaisesRegex(RegistrationRejected, 'dangles'):
                    merge_routes(self.revisions, 'A', pid, lid, rid,
                                 left_renames={'helper': 'scan'}, right_renames={}, decisions=decisions)
        self.assertEqual(self.revisions.db.execute('SELECT count(*) FROM route_revisions').fetchone()[0], before)
        self.assertEqual(self.revisions.db.execute('SELECT count(*) FROM route_merges').fetchone()[0], 0)


class MergeDecisionTests(unittest.TestCase):
    def setUp(self):
        self.workbench = Workbench(self)
        self.revisions, self.evidence = self.workbench.site('A')

    def _publish(self, parent, left, right):
        pid = register_route(self.revisions, parent)
        lid = register_route(self.revisions, left, pid)
        rid = register_route(self.revisions, right, pid)
        return pid, lid, rid

    def test_presence_and_action_conflicts_share_consecutive_numbering(self):
        hold = (RouteAction('hold', 1, 0),)
        rel0 = (RouteAction('release', 1, 0),)
        rel1 = (RouteAction('release', 1, 1),)
        parent = RouteDraft('A', {'intake': 'keep', 'side': 'gone'}, {'keep': rel0, 'gone': hold})
        left = RouteDraft('A', {'intake': 'keep'}, {'keep': rel1})
        right = RouteDraft('A', {'intake': 'keep', 'side': 'gone'},
                           {'keep': (RouteAction('release', '1/2', 0), RouteAction('hold', '1/2', 0)),
                            'gone': rel1})
        pid, lid, rid = self._publish(parent, left, right)
        revision = merge_routes(
            self.revisions, 'A', pid, lid, rid, left_renames={}, right_renames={},
            decisions={('cell', 'gone', 'presence'): 'right', ('cell', 'keep', 'actions'): 'left'},
        )
        merged = self.revisions.get('A', revision)
        self.assertEqual(set(merged.draft.steps), {'keep', 'gone'})
        self.assertEqual(merged.draft.steps['keep'][0].minutes, 1)
        block = json.loads(freeze_station(self.revisions, self.evidence, 'A', revision))['body']['merge']
        self.assertEqual(block['decisions'],
                         [[1, 'cell', 'presence', 'right'], [2, 'cell', 'actions', 'left']])

    def test_entry_target_conflict_can_delete_or_retarget(self):
        hold = (RouteAction('hold', 1, 0),)
        parent = RouteDraft('A', {'intake': 'a'}, {'a': hold, 'b': hold, 'c': hold})
        left = RouteDraft('A', {'intake': 'b'}, {'a': hold, 'b': hold, 'c': hold})
        right = RouteDraft('A', {'intake': 'c'}, {'a': hold, 'b': hold, 'c': hold})
        pid, lid, rid = self._publish(parent, left, right)
        revision = merge_routes(
            self.revisions, 'A', pid, lid, rid, left_renames={}, right_renames={},
            decisions={('entry', 'intake', 'target'): 'right'},
        )
        self.assertEqual(self.revisions.get('A', revision).draft.entries['intake'], 'c')

    def test_entry_deletion_through_missing_side_decision(self):
        hold = (RouteAction('hold', 1, 0),)
        parent = RouteDraft('A', {'intake': 'a', 'old': 'b'}, {'a': hold, 'b': hold})
        left = RouteDraft('A', {'intake': 'a'}, {'a': hold, 'b': hold})
        right = RouteDraft('A', {'intake': 'a', 'old': 'a'}, {'a': hold, 'b': hold})
        pid, lid, rid = self._publish(parent, left, right)
        with self.assertRaises(RegistrationRejected):
            merge_routes(self.revisions, 'A', pid, lid, rid,
                         left_renames={}, right_renames={}, decisions={})
        deleted = merge_routes(
            self.revisions, 'A', pid, lid, rid, left_renames={}, right_renames={},
            decisions={('entry', 'old', 'target'): 'left'},
        )
        self.assertNotIn('old', self.revisions.get('A', deleted).draft.entries)
        kept = merge_routes(
            self.revisions, 'A', pid, lid, rid, left_renames={}, right_renames={},
            decisions={('entry', 'old', 'target'): 'right'},
        )
        self.assertEqual(self.revisions.get('A', kept).draft.entries['old'], 'a')

    def test_untouched_side_entry_deletion_merges_without_decision(self):
        hold = (RouteAction('hold', 1, 0),)
        parent = RouteDraft('A', {'intake': 'a', 'old': 'b'}, {'a': hold, 'b': hold})
        left = RouteDraft('A', {'intake': 'a'}, {'a': hold, 'b': hold})
        pid, lid, rid = self._publish(parent, left, parent)
        revision = merge_routes(self.revisions, 'A', pid, lid, rid,
                                left_renames={}, right_renames={}, decisions={})
        self.assertEqual(dict(self.revisions.get('A', revision).draft.entries), {'intake': 'a'})

    def test_double_rename_chooses_a_side(self):
        hold = (RouteAction('hold', 1, 0),)
        parent = RouteDraft('A', {'intake': 'x'}, {'x': hold})
        left = RouteDraft('A', {'intake': 'lx'}, {'lx': hold})
        right = RouteDraft('A', {'intake': 'rx'}, {'rx': hold})
        pid, lid, rid = self._publish(parent, left, right)
        revision = merge_routes(
            self.revisions, 'A', pid, lid, rid,
            left_renames={'x': 'lx'}, right_renames={'x': 'rx'},
            decisions={('cell', 'x', 'name'): 'right'},
        )
        merged = self.revisions.get('A', revision)
        self.assertEqual(list(merged.draft.steps), ['rx'])
        self.assertEqual(merged.draft.entries['intake'], 'rx')


class MergeIssueAndReportTests(unittest.TestCase):
    def setUp(self):
        self.workbench = Workbench(self)

    def _merged_piece(self, local_jump, decisions):
        revisions, evidence = self.workbench.site('A')
        parent, left, right, peer = parallel_drafts(local_jump=local_jump)
        pid = register_route(revisions, parent)
        lid = register_route(revisions, left, pid)
        rid = register_route(revisions, right, pid)
        mid = merge_routes(revisions, 'A', pid, lid, rid,
                           left_renames={'scan': 'verify'}, right_renames={}, decisions=decisions)
        merged_raw = freeze_station(revisions, evidence, 'A', mid)
        peer_revisions, peer_evidence = self.workbench.site('B')
        peer_id = register_route(peer_revisions, peer)
        peer_raw = freeze_station(peer_revisions, peer_evidence, 'B', peer_id)
        return mid, merged_raw, peer_id, peer_raw

    def test_format_two_piece_carries_bound_provenance_and_keeps_names_private(self):
        _, raw, _, _ = self._merged_piece(False, {})
        body = json.loads(raw)['body']
        self.assertEqual(body['format'], 2)
        self.assertEqual(body['merge']['station'], 'A')
        self.assertEqual(body['merge']['revision'], body['revision'])
        self.assertEqual(body['merge']['decisions'], [])
        self.assertNotIn(b'scan', raw)
        self.assertNotIn(b'verify', raw)
        package = decode_package(raw)
        self.assertEqual(package.artifact.merge.decisions, ())
        # Ordinary pieces still use format one without a merge block.
        revisions, evidence = self.workbench.site('A')
        ordinary = freeze_station(revisions, evidence, 'A', 1)
        ordinary_body = json.loads(ordinary)['body']
        self.assertEqual(ordinary_body['format'], 1)
        self.assertNotIn('merge', ordinary_body)

    def test_local_jump_remaps_to_the_real_surviving_cell(self):
        _, raw, _, _ = self._merged_piece(True, {})
        backup = {entry.name: entry for entry in decode_package(raw).artifact.entries}['backup']
        self.assertEqual(backup.unexited, 1)
        self.assertEqual(backup.exits, ())

    def test_tampered_provenance_and_format_mismatch_are_rejected(self):
        _, raw, _, _ = self._merged_piece(False, {})
        tampered = json.loads(raw)
        tampered['body']['merge']['parent'] = 999
        tampered['digest'] = evidence_digest(tampered['body'])
        with self.assertRaises(ReceiptRejected):
            receive_station_piece(self.workbench.receipts, canonical_json(tampered))
        mismatched = json.loads(raw)
        mismatched['body']['format'] = 1
        mismatched['digest'] = evidence_digest(mismatched['body'])
        with self.assertRaises(ReceiptRejected):
            receive_station_piece(self.workbench.receipts, canonical_json(mismatched))
        self.assertEqual(self.workbench.center.execute('SELECT count(*) FROM received_pieces').fetchone()[0], 0)

    def test_closed_station_two_port_report_and_recovery_by_number(self):
        revision, merged_raw, peer_revision, peer_raw = self._merged_piece(True, {})
        merged = receive_station_piece(self.workbench.receipts, merged_raw)
        # Re-delivery of the same bytes reuses the receipt id.
        self.assertEqual(receive_station_piece(self.workbench.receipts, merged_raw).receipt_id,
                         merged.receipt_id)
        peer = receive_station_piece(self.workbench.receipts, peer_raw)
        pair = self.workbench.pair(merged_raw, peer_raw)
        self.workbench.disconnect_sources()
        self.workbench.reopen_center()

        observed = {}
        last = None
        for entry in ('intake', 'backup'):
            signed = [report_turnaround(self.workbench.pairs, self.workbench.reports,
                                        ReportRequest(pair.pair_id, 'A', entry, minute))
                      for minute in (0, 1)]
            last = signed[-1]
            observed[entry] = [str(report.result.by_deadline) for report in signed] + [str(last.result.eventual)]
        self.assertEqual(observed, {'intake': ['0', '1', '1'], 'backup': ['0', '0', '0']})

        report = read_report(self.workbench.reports, last.report_id)
        first_original, second_original = report.originals
        self.assertEqual((first_original, second_original), (merged_raw, peer_raw))
        recovered_body = json.loads(first_original)['body']
        self.assertEqual(recovered_body['format'], 2)
        self.assertEqual((recovered_body['merge']['parent'],
                          recovered_body['merge']['left'],
                          recovered_body['merge']['right']), (1, 2, 3))
        self.assertEqual(recovered_body['merge']['revision'], revision)
        exported = json.loads(export_report(self.workbench.reports, last.report_id))
        self.assertEqual(tuple(b64decode(blob) for blob in exported['originals']),
                         (merged_raw, peer_raw))
        self.assertEqual([source.revision for source in report.result.sources],
                         [revision, peer_revision])


if __name__ == '__main__':
    unittest.main()
