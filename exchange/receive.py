from exchange.package import PackageError, decode_package
from exchange.receipt import ReceiptConflictError, ReceiptStore, ReceivedPiece


class ReceiptRejected(ValueError):
    pass


def receive_station_piece(store: ReceiptStore, raw: bytes) -> ReceivedPiece:
    try:
        return store.record(decode_package(raw))
    except (PackageError, ReceiptConflictError) as error:
        raise ReceiptRejected(str(error)) from error
