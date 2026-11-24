import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from exchange.pair import PairStore, pair_received
from exchange.receipt import ReceiptStore
from exchange.receive import receive_station_piece
from reports.store import ReportStore
from review.request import ReportRequest, report_turnaround
from routes.intake import register_route
from routes.model import RouteAction, RouteDraft
from routes.revisions import RevisionStore
from station.evidence_store import EvidenceStore
from station.issue import freeze_station


def single(station: str, *actions: RouteAction, entry='intake') -> RouteDraft:
    return RouteDraft(station, {entry: 'private_cell'}, {'private_cell': actions})


class Workbench:
    def __init__(self, case):
        temporary = TemporaryDirectory(prefix='turnaround-test-')
        case.addCleanup(temporary.cleanup)
        case.addCleanup(self.close)
        self.directory = Path(temporary.name)
        self.sources = {}
        self.center = None
        self.reopen_center()

    def reopen_center(self):
        if self.center is not None:
            self.center.close()
        self.center = sqlite3.connect(self.directory / 'center.sqlite')
        self.receipts = ReceiptStore(self.center)
        self.pairs = PairStore(self.center)
        self.reports = ReportStore(self.center)
        for store in (self.receipts, self.pairs, self.reports):
            store.initialize()

    def site(self, station):
        if station not in self.sources:
            self.sources[station] = sqlite3.connect(self.directory / f'{station}.sqlite')
        db = self.sources[station]
        revisions, evidence = RevisionStore(db), EvidenceStore(db)
        revisions.initialize()
        evidence.initialize()
        return revisions, evidence

    def close_site(self, station):
        self.sources.pop(station).close()

    def disconnect_sources(self):
        for station in tuple(self.sources):
            self.close_site(station)
            path = self.directory / f'{station}.sqlite'
            path.rename(path.with_suffix('.closed'))

    def close(self):
        for station in tuple(self.sources):
            self.close_site(station)
        if self.center is not None:
            self.center.close()
            self.center = None

    def publish(self, draft, parent=None):
        revisions, evidence = self.site(draft.station)
        revision = register_route(revisions, draft, parent)
        return revision, freeze_station(revisions, evidence, draft.station, revision)

    def receive(self, raw):
        return receive_station_piece(self.receipts, raw)

    def pair(self, first, second):
        receipts = [self.receive(raw).receipt_id for raw in (first, second)]
        return pair_received(self.pairs, *receipts)

    def sign(self, pair_id, deadline, station='A', entry='intake'):
        request = ReportRequest(pair_id, station, entry, deadline)
        return report_turnaround(self.pairs, self.reports, request)
