"""Inputs and V1 observations for the parallel corrections example."""
import json
import sqlite3
from contextlib import closing

from exchange.pair import PairStore, pair_received
from exchange.receipt import ReceiptStore
from exchange.receive import receive_station_piece
from reports.history import read_report
from reports.store import ReportStore
from review.request import ReportRequest, report_turnaround
from routes.intake import register_route
from routes.model import RouteAction, RouteDraft
from routes.revisions import RevisionStore
from station.evidence_store import EvidenceStore
from station.issue import freeze_station


def parallel_drafts(local_jump=False):
    release_now = (RouteAction("release", 1, 0),)
    release_later = (RouteAction("release", 1, 1),)
    hold = (RouteAction("hold", 1, 0),)
    parent = RouteDraft("A", {"intake": "scan"}, {"scan": release_now})
    left_entries = {"intake": "verify", "backup": "scan"}
    left_steps = {"verify": release_now, "scan": hold}
    if local_jump:
        left_entries["backup"] = "staging"
        left_steps["staging"] = (RouteAction("step", 1, 0, "scan"),)
    left = RouteDraft("A", left_entries, left_steps)
    right = RouteDraft("A", {"intake": "scan"}, {"scan": release_later})
    peer = RouteDraft("B", {"intake": "rest"}, {"rest": hold})
    return parent, left, right, peer


def dangling_drafts():
    release = (RouteAction("release", 1, 0),)
    hold = (RouteAction("hold", 1, 0),)
    later = (RouteAction("release", 1, 1),)
    parent = RouteDraft("A", {"intake": "gate"}, {"gate": release, "scan": hold, "helper": later})
    left = RouteDraft("A", {"intake": "gate"}, {"gate": release, "scan": later})
    right = RouteDraft("A", {"intake": "gate", "backup": "scan"}, parent.steps)
    return parent, left, right


def _issue(drafts):
    with closing(sqlite3.connect(":memory:")) as db:
        revisions, evidence = RevisionStore(db), EvidenceStore(db)
        revisions.initialize()
        evidence.initialize()
        parent = register_route(revisions, drafts[0])
        children = [register_route(revisions, draft, parent) for draft in drafts[1:]]
        return [freeze_station(revisions, evidence, drafts[0].station, rev) for rev in (parent, *children)]


def observe_baseline():
    *drafts, peer = parallel_drafts(local_jump=True)
    raws = _issue(drafts)
    (peer_raw,) = _issue((peer,))
    actual = {}
    with closing(sqlite3.connect(":memory:")) as db:
        receipts, pairs, reports = ReceiptStore(db), PairStore(db), ReportStore(db)
        for store in (receipts, pairs, reports):
            store.initialize()
        peer_id = receive_station_piece(receipts, peer_raw).receipt_id
        for label, draft, raw in zip(("parent", "left", "right"), drafts, raws):
            source_id = receive_station_piece(receipts, raw).receipt_id
            body = json.loads(raw)["body"]
            if body["format"] != 1 or "merge" in body:
                raise AssertionError("ordinary issue should still be V1")
            pair = pair_received(pairs, source_id, peer_id)
            entries = {}
            for entry in draft.entries:
                signed = [report_turnaround(pairs, reports, ReportRequest(pair.pair_id, "A", entry, t))
                          for t in (0, 1)]
                if any(read_report(reports, r.report_id).originals != (raw, peer_raw) for r in signed):
                    raise AssertionError("archive originals diverged from the selected pair")
                entries[entry] = [str(r.result.by_deadline) for r in signed] + [str(signed[-1].result.eventual)]
            actual[label] = entries
    expected = {"parent": {"intake": ["1", "1", "1"]},
                "left": {"intake": ["1", "1", "1"], "backup": ["0", "0", "0"]},
                "right": {"intake": ["0", "1", "1"]}}
    if actual != expected:
        raise AssertionError(f"ordinary branch results diverged: {actual!r}")
    return actual


if __name__ == "__main__":
    print(json.dumps(observe_baseline(), sort_keys=True))
