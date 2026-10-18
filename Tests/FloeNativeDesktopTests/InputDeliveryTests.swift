import ApplicationServices
import XCTest
@testable import FloeNativeDesktop

final class InputDeliveryTests: XCTestCase {
    func testReceiptMatchesOnlyTheOutstandingEvent() {
        var receipt = NativeInputReceipt()
        receipt.begin(marker: 21)
        receipt.receive(20)
        XCTAssertFalse(receipt.observed)
        receipt.receive(21)
        XCTAssertTrue(receipt.observed)
        receipt.finish()
        receipt.begin(marker: 22)
        receipt.receive(21)
        receipt.receive(0)
        XCTAssertFalse(receipt.observed)
        receipt.receive(22)
        XCTAssertTrue(receipt.observed)
    }

    func testCleanupMarkersCannotMatchSubsequentInput() throws {
        let event = try XCTUnwrap(CGEvent(source: nil))
        try NativeInputDelivery.markAuxiliary(event)
        let first = event.getIntegerValueField(.eventSourceUserData)
        try NativeInputDelivery.markAuxiliary(event)
        let second = event.getIntegerValueField(.eventSourceUserData)
        XCTAssertNotEqual(first, second)
        XCTAssertNotEqual(first, 0)
        var receipt = NativeInputReceipt()
        receipt.begin(marker: second)
        receipt.receive(first)
        XCTAssertFalse(receipt.observed)
    }

    func testCancellationBeforePostingDoesNotInjectInput() throws {
        enum Cancelled: Error { case stopped }
        let delivery = NativeInputDelivery()
        let event = try XCTUnwrap(CGEvent(source: nil))
        XCTAssertThrowsError(try delivery.post(event, check: { throw Cancelled.stopped }))
        XCTAssertEqual(delivery.progress, .init(posted: 0, observed: 0))
    }
}
