import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from exchange.package import decode_package
from exchange.pair import PairStore, pair_received
from exchange.receipt import ReceiptStore
from exchange.receive import receive_station_piece
from reports.export import export_report
from reports.history import read_report
from reports.store import ReportStore
from review.request import ReportRequest, report_turnaround
from routes.intake import merge_routes, register_route
from routes.model import RouteAction, RouteDraft
from routes.revisions import RevisionStore
from samples.merge_case import parallel_drafts
from station.evidence_store import EvidenceStore
from station.issue import freeze_station


def sample_routes() -> tuple[RouteDraft, RouteDraft, RouteDraft]:
    path = Path(__file__).with_name('samples') / 'day.json'
    raw = json.loads(path.read_text(encoding='utf-8'))
    drafts = []
    for station, key in (('A', 'A'), ('A', 'A_correction'), ('B', 'B')):
        route = raw[key]
        steps = {name: tuple(RouteAction(*action) for action in actions) for name, actions in route['steps'].items()}
        drafts.append(RouteDraft(station, route['entries'], steps))
    return tuple(drafts)


def _site_pieces(path: Path, original: RouteDraft, correction: RouteDraft | None = None) -> list[bytes]:
    with closing(sqlite3.connect(path)) as db:
        revisions, evidence = RevisionStore(db), EvidenceStore(db)
        revisions.initialize()
        evidence.initialize()
        original_id = register_route(revisions, original)
        selected_ids = [original_id]
        if correction is not None:
            selected_ids.append(register_route(revisions, correction, original_id))
        return [freeze_station(revisions, evidence, original.station, revision_id) for revision_id in selected_ids]


def run_demo(directory: Path) -> dict:
    a, correction, b = sample_routes()
    old_a, new_a = _site_pieces(directory / 'a.sqlite', a, correction)
    (old_b,) = _site_pieces(directory / 'b.sqlite', b)
    center = directory / 'center.sqlite'
    with closing(sqlite3.connect(center)) as db:
        receipts, pairs, reports = ReceiptStore(db), PairStore(db), ReportStore(db)
        for repository in (receipts, pairs, reports):
            repository.initialize()
        old, new, peer = [receive_station_piece(receipts, raw) for raw in (old_a, new_a, old_b)]
        pair = pair_received(pairs, old.receipt_id, peer.receipt_id)
        revised_pair = pair_received(pairs, new.receipt_id, peer.receipt_id)
        for station in ('a', 'b'):
            source = directory / f'{station}.sqlite'
            source.rename(source.with_suffix('.closed'))
        first = report_turnaround(pairs, reports, ReportRequest(pair.pair_id, 'A', 'intake', 0))
        next_minute = report_turnaround(pairs, reports, ReportRequest(pair.pair_id, 'A', 'intake', 1))
        revised = report_turnaround(pairs, reports, ReportRequest(revised_pair.pair_id, 'A', 'intake', 0))
        archive = export_report(reports, first.report_id)
    with closing(sqlite3.connect(center)) as db:
        reports = ReportStore(db)
        reports.initialize()
        recovered = read_report(reports, first.report_id)
        if recovered != first or export_report(reports, first.report_id) != archive:
            raise AssertionError('the reopened archive diverged from the original report')

    actual = {
        'minute_zero': str(first.result.by_deadline),
        'minute_one': str(next_minute.result.by_deadline),
        'eventual': str(first.result.eventual),
        'corrected_minute_zero': str(revised.result.by_deadline),
    }
    expected = {'minute_zero': '2/7', 'minute_one': '22/49', 'eventual': '2/3', 'corrected_minute_zero': '2/3'}
    originals = (old_a, old_b)
    downstream_expected = {
        'receipt': first.receipt.receipt_code,
        'piece_ids': [decode_package(raw).artifact.identity.piece_id for raw in originals],
        'original_bytes_preserved': True,
    }
    downstream_actual = {
        'receipt': recovered.receipt.receipt_code,
        'piece_ids': [origin.piece_id for origin in recovered.result.sources],
        'original_bytes_preserved': recovered.originals == originals,
    }
    if actual != expected or downstream_actual != downstream_expected:
        raise AssertionError((actual, downstream_actual))
    return {
        'case_id': 'N7',
        'input': {'sample': 'samples/day.json', 'start': ['A', 'intake'], 'deadlines': [0, 1], 'sources_closed': True},
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': ['station/first_exit.py', 'exchange/pair.py', 'review/turnaround.py', 'review/eventual.py', 'reports/store.py'],
        'oracle': 'Manual per-attempt release 1/4, immediate return 1/8 and one-minute retry 1/2. Archive compared to original receipt and incoming bytes.',
    }


def run_merge_demo(directory: Path) -> dict:
    """Issue a format-two merged correction and report both ports after close-out."""
    parent, left, right, peer = parallel_drafts(local_jump=True)
    site_path = directory / 'merge_a.sqlite'
    with closing(sqlite3.connect(site_path)) as db:
        revisions, evidence = RevisionStore(db), EvidenceStore(db)
        revisions.initialize()
        evidence.initialize()
        parent_id = register_route(revisions, parent)
        left_id = register_route(revisions, left, parent_id)
        right_id = register_route(revisions, right, parent_id)
        # The left draft renames the original inspection cell; the right draft
        # delays its release by one minute. The rename merges without a decision.
        merged_id = merge_routes(
            revisions, 'A', parent_id, left_id, right_id,
            left_renames={'scan': 'verify'}, right_renames={}, decisions={},
        )
        merged_raw = freeze_station(revisions, evidence, 'A', merged_id)
        peer_id = register_route(revisions, peer)
        peer_raw = freeze_station(revisions, evidence, 'B', peer_id)
        issued_body = json.loads(merged_raw)['body']
        issued_merge = issued_body['merge']

    center = directory / 'merge_center.sqlite'
    with closing(sqlite3.connect(center)) as db:
        receipts, pairs, reports = ReceiptStore(db), PairStore(db), ReportStore(db)
        for repository in (receipts, pairs, reports):
            repository.initialize()
        merged_piece = receive_station_piece(receipts, merged_raw)
        # Re-delivery of the identical original reuses the same receipt number.
        redelivered = receive_station_piece(receipts, merged_raw)
        peer_piece = receive_station_piece(receipts, peer_raw)
        pair = pair_received(pairs, merged_piece.receipt_id, peer_piece.receipt_id)
        site_path.rename(site_path.with_suffix('.closed'))

        observed = {}
        last_report = None
        for entry in ('intake', 'backup'):
            signed = [
                report_turnaround(pairs, reports, ReportRequest(pair.pair_id, 'A', entry, minute))
                for minute in (0, 1)
            ]
            last_report = signed[-1]
            observed[entry] = [str(report.result.by_deadline) for report in signed] + [
                str(last_report.result.eventual)
            ]
        archive = export_report(reports, last_report.report_id)
        report_id = last_report.report_id

    # The source library is gone: reopen only the center and trace the final
    # report number back to the actual parent, children and consumed decisions.
    with closing(sqlite3.connect(center)) as db:
        reports = ReportStore(db)
        reports.initialize()
        recovered = read_report(reports, report_id)
        if export_report(reports, report_id) != archive:
            raise AssertionError('the reopened merge archive diverged from the original report')
        merged_original, peer_original = recovered.originals
        merge_block = json.loads(merged_original)['body']['merge']

    expected_ports = {'intake': ['0', '1', '1'], 'backup': ['0', '0', '0']}
    if observed != expected_ports:
        raise AssertionError((observed, expected_ports))
    if merge_block != issued_merge:
        raise AssertionError('recovered provenance diverged from the issued format-two piece')
    return {
        'case_id': 'N7-MERGE',
        'input': {
            'sample': 'samples.merge_case.parallel_drafts(local_jump=True)',
            'parent': parent_id, 'left': left_id, 'right': right_id, 'merged': merged_id,
            'left_renames': {'scan': 'verify'}, 'decisions': [],
            'start': ['A', 'intake'], 'deadlines': [0, 1], 'sources_closed': True,
        },
        'expected_ports': expected_ports,
        'actual_ports': observed,
        'receipt_reused': redelivered.receipt_id == merged_piece.receipt_id,
        'format_two': {
            'format': issued_body['format'],
            'merge': issued_merge,
            'private_names_absent': all(
                name.encode('ascii') not in merged_raw for name in ('scan', 'verify', 'staging')
            ),
        },
        'recovered_by_report_number': {
            'report_id': report_id,
            'receipt': recovered.receipt.receipt_code,
            'parent': merge_block['parent'],
            'left': merge_block['left'],
            'right': merge_block['right'],
            'decisions': merge_block['decisions'],
            'sources': [(source.station, source.revision) for source in recovered.result.sources],
            'originals_match': (merged_original, peer_original) == (merged_raw, peer_raw),
        },
        'code_paths': ['routes/merger.py', 'routes/intake.py', 'station/issue.py',
                       'exchange/package.py', 'reports/store.py'],
    }


def main() -> None:
    with TemporaryDirectory(prefix='turnaround-demo-') as temporary:
        result = run_demo(Path(temporary))
        print('POINTWISE_SCENARIO ' + json.dumps(result, sort_keys=True))
        merge_result = run_merge_demo(Path(temporary))
        print('POINTWISE_SCENARIO ' + json.dumps(merge_result, sort_keys=True))


if __name__ == '__main__':
    main()
