import XCTest
@testable import FloeNativeDesktop

final class CaptureTests: XCTestCase {
    func testSourcePixelsAreNotInventedByPicturePreferences() throws {
        let settings = try NativeCaptureSettings(request: ["pixel_ratio": 4, "max_dimension": 2560])
        XCTAssertEqual(settings.dimensions(points: CGSize(width: 1920, height: 1080), sourceScale: 1), CGSize(width: 1920, height: 1080))
        XCTAssertEqual(settings.dimensions(points: CGSize(width: 1920, height: 1080), sourceScale: 2), CGSize(width: 2560, height: 1440))
    }

    func testInvalidPictureDoesNotReachNativeCapture() {
        for request: [String: Any] in [["frame_rate": 1000], ["max_dimension": 8192], ["pixel_ratio": true], ["mode": "unknown"]] {
            XCTAssertThrowsError(try NativeCaptureSettings(request: request))
        }
    }

    func testNativePixelsPreserveSourceResolutionAbovePictureLimit() throws {
        let settings = try NativeCaptureSettings(request: ["native_pixels": true, "max_dimension": 1920])
        XCTAssertEqual(settings.dimensions(points: CGSize(width: 2304, height: 1296), sourceScale: 2),
                       CGSize(width: 4608, height: 2592))
    }

    func testUnicodeConstructionNeverPostsInput() throws {
        let events = try NativeDesktopInput.text("A中😀\n")
        XCTAssertEqual(events.count, 8)
        XCTAssertEqual(events.last?.getIntegerValueField(.keyboardEventKeycode), 36)
    }
}

final class KeyboardTests: XCTestCase {
    func testPhysicalKeyCodeDoesNotFollowTheClientKeyboardCharacter() throws {
        let packet: [String: Any] = ["key":"q", "code":"KeyA", "pressed":true, "repeat":false,
            "shiftKey":false, "ctrlKey":false, "altKey":false, "metaKey":false]
        let event = try NativeDesktopInput.viewerKey(packet, physical: true)
        XCTAssertEqual(event.getIntegerValueField(.keyboardEventKeycode), 0)
        var keypad = packet
        keypad["key"] = "1"; keypad["code"] = "Numpad1"
        XCTAssertEqual(try NativeDesktopInput.viewerKey(keypad, physical: true).getIntegerValueField(.keyboardEventKeycode), 83)
        keypad["code"] = "Unidentified"
        XCTAssertThrowsError(try NativeDesktopInput.viewerKey(keypad, physical: true))
    }
}
