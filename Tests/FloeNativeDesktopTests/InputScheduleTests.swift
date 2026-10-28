import ApplicationServices
import XCTest
@testable import FloeNativeDesktop

final class InputScheduleTests: XCTestCase {
    enum Stop: Error { case cancelled, unknownDelivery }
    func testDurationBoundsPathAndZeroDelay() throws {
        for ms in [0, 1, 500, 5000] {
            let sequence = try NativeDesktopInput.dragSequence(from: CGPoint(x: 10, y: 20), to: CGPoint(x: 110, y: 120), durationMilliseconds: ms)
            var clock = 10.0
            var received: [(CGEventType, Double)] = []
            var waits = 0
            try sequence.deliver(now: { clock }, wait: { clock += $0; waits += 1 }, check: {}, post: { received.append(($0.type, clock)) })
            XCTAssertEqual(sequence.events.count, max(1, min(120, ms / 16)) + 3)
            XCTAssertEqual(received.first?.0, .mouseMoved)
            XCTAssertEqual(received[1].0, .leftMouseDown)
            XCTAssertEqual(received.last?.0, .leftMouseUp)
            XCTAssertEqual(received.last!.1 - received[1].1, Double(ms) / 1000, accuracy: 0.000001)
            for (index, event) in sequence.events.dropFirst(2).dropLast().enumerated() {
                let fraction = Double(index + 1) / Double(sequence.events.count - 3)
                XCTAssertEqual(event.location.x, 10 + 100 * fraction, accuracy: 0.000001)
                XCTAssertEqual(event.location.y, 20 + 100 * fraction, accuracy: 0.000001)
            }
            if ms == 0 { XCTAssertEqual(waits, 0) }
        }
        for ms in [-1, 5001, Int.max] {
            XCTAssertThrowsError(try NativeDesktopInput.dragSequence(from: .zero, to: .zero, durationMilliseconds: ms))
        }
    }
    func testReceiptOverheadDoesNotAccumulateAsExtraSleeps() throws {
        let sequence = try NativeDesktopInput.dragSequence(from: .zero, to: CGPoint(x: 100, y: 80), durationMilliseconds: 1000)
        var clock = 30.0, down = 0.0, up = 0.0
        try sequence.deliver(now: { clock }, wait: { clock += $0 }, check: {}, post: { event in
            if event.type == .leftMouseUp { up = clock }
            clock += 0.005
            if event.type == .leftMouseDown { down = clock }
        })
        XCTAssertEqual(up - down, 1.005, accuracy: 0.000001)
    }
    func testSlowDeliveryDoesNotReplayOrDropEvents() throws {
        let sequence = try NativeDesktopInput.dragSequence(from: .zero, to: .zero, durationMilliseconds: 64)
        var clock = 0.0
        var posted: [CGEventType] = []
        try sequence.deliver(now: { clock }, wait: { clock += $0 }, check: {}, post: { posted.append($0.type); clock += 0.2 })
        XCTAssertEqual(posted, sequence.events.map(\.type))
        XCTAssertGreaterThan(clock, 0.064)
    }
    func testCancellationDuringWaitStopsBeforeNextInput() throws {
        let sequence = try NativeDesktopInput.dragSequence(from: .zero, to: .zero, durationMilliseconds: 5000)
        var clock = 0.0, largestWait = 0.0
        var posted: [CGEventType] = []
        XCTAssertThrowsError(try sequence.deliver(now: { clock }, wait: {
            largestWait = max(largestWait, $0); clock += $0
        }, check: {
            if clock >= 0.02 { throw Stop.cancelled }
        }, post: { posted.append($0.type) }))
        XCTAssertEqual(posted, [.mouseMoved, .leftMouseDown])
        XCTAssertLessThanOrEqual(largestWait, 0.01)
        XCTAssertLessThanOrEqual(clock, 0.03)
    }
    func testUnknownDeliveryStopsWithoutReplayOrRelease() throws {
        let sequence = try NativeDesktopInput.dragSequence(from: .zero, to: .zero, durationMilliseconds: 500)
        var clock = 0.0
        var posted: [CGEventType] = []
        XCTAssertThrowsError(try sequence.deliver(now: { clock }, wait: { clock += $0 }, check: {}, post: {
            posted.append($0.type)
            if $0.type == .leftMouseDown { throw Stop.unknownDelivery }
        }))
        XCTAssertEqual(posted, [.mouseMoved, .leftMouseDown])
    }
    func testImmediateSequenceAndCancellationBeforeInput() throws {
        let events = try NativeDesktopInput.click(at: .zero, count: 1)
        let sequence = NativeInputSequence(events: events)
        var posted = 0
        try sequence.deliver(now: { 0 }, wait: { _ in XCTFail("unexpected delay") }, check: {}, post: { _ in posted += 1 })
        XCTAssertEqual(posted, events.count)
        XCTAssertThrowsError(try sequence.deliver(now: { 0 }, wait: { _ in XCTFail("unexpected delay") }, check: { throw Stop.cancelled }, post: { _ in XCTFail("input after stop") }))
    }
}
