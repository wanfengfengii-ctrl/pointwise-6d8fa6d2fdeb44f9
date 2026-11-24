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
from routes.intake import register_route
from routes.model import RouteAction, RouteDraft
from routes.revisions import RevisionStore
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


def main() -> None:
    with TemporaryDirectory(prefix='turnaround-demo-') as temporary:
        result = run_demo(Path(temporary))
        print('POINTWISE_SCENARIO ' + json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
