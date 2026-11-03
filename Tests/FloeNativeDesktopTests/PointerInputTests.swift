import ApplicationServices
import XCTest
@testable import FloeNativeDesktop

final class PointerInputTests: XCTestCase {
    func testButtonsModifiersAndHold() throws {
        for button in [CGMouseButton.left, .right, .center] {
            let sequence = try NativeDesktopInput.clickSequence(at: CGPoint(x: 12, y: 24), button: button, count: 3, modifiers: [.maskShift, .maskCommand], holdMilliseconds: 20)
            XCTAssertEqual(sequence.events.count, 7)
            for event in sequence.events.dropFirst() {
                XCTAssertEqual(event.getIntegerValueField(.mouseEventButtonNumber), Int64(button.rawValue))
                XCTAssertTrue(event.flags.contains(.maskShift))
                XCTAssertTrue(event.flags.contains(.maskCommand))
            }
            XCTAssertEqual(sequence.events.last?.getIntegerValueField(.mouseEventClickState), 3)
        }
        XCTAssertThrowsError(try NativeDesktopInput.clickSequence(at: .zero, count: 0))
        XCTAssertThrowsError(try NativeDesktopInput.clickSequence(at: .zero, holdMilliseconds: 5001))
    }
    func testPointerMoveAndDragPreserveButton() throws {
        let move = try NativeDesktopInput.pointer(at: .zero)
        XCTAssertEqual(move.type, .mouseMoved)
        let drag = try NativeDesktopInput.dragSequence(from: .zero, to: CGPoint(x: 90, y: 90), durationMilliseconds: 10, button: .right, modifiers: .maskAlternate)
        XCTAssertEqual(drag.events[1].type, .rightMouseDown)
        XCTAssertEqual(drag.events[2].type, .rightMouseDragged)
        XCTAssertEqual(drag.events.last?.type, .rightMouseUp)
        XCTAssertTrue(drag.events.allSatisfy { $0.flags.contains(.maskAlternate) })
    }
}
