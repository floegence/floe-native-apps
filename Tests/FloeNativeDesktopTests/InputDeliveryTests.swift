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

extension InputDeliveryTests {
    func testDirectedInputRequiresMatchingLiveWindow() throws {
        let event = try NativeDesktopInput.pointer(at: .zero, pressed: true)
        let delivery = NativeInputDelivery(pid: getpid(), window: 0, directed: true)
        XCTAssertFalse(delivery.available)
        XCTAssertThrowsError(try delivery.post(event))
        XCTAssertThrowsError(try delivery.postAuxiliary(event))
        XCTAssertEqual(delivery.progress.posted, 0)
        XCTAssertFalse(NativeInputDelivery(directed: true).available)
    }
    func testExplicitInputScheduleIsBounded() throws {
        let events = try NativeDesktopInput.key("Meta+c")
        let sequence = try NativeInputSequence(events: events, delaysMilliseconds: [10, 20])
        var clock: TimeInterval = 0
        var times: [TimeInterval] = []
        try sequence.deliver(now: { clock }, wait: { clock += $0 }, check: {}, post: { _ in times.append(clock) })
        XCTAssertEqual(times.count, 2)
        XCTAssertEqual(times[0], 0.01, accuracy: 0.000001)
        XCTAssertEqual(times[1], 0.03, accuracy: 0.000001)
        XCTAssertThrowsError(try NativeInputSequence(events: events, delaysMilliseconds: [0]))
        XCTAssertThrowsError(try NativeInputSequence(events: events, delaysMilliseconds: [-1, 0]))
    }
}
