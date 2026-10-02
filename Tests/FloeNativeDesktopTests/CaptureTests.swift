import XCTest
import VideoToolbox
@testable import FloeNativeDesktop

final class CaptureTests: XCTestCase {
    func testDisplayCanReturnAfterTheLastMonitorWasRemoved() {
        let removed = nativeDesktopDisplayAfterChange(4, available: [])
        XCTAssertNil(removed)
        XCTAssertEqual(nativeDesktopDisplayAfterChange(removed, available: [8]), 8)
        XCTAssertEqual(nativeDesktopDisplayAfterChange(8, available: [4, 8]), 8)
        XCTAssertEqual(nativeDesktopDisplayAfterChange(8, available: [4]), 4)
    }

    func testSlowEncoderReceivesOnlyNewestUnencodedSample() throws {
        let mailbox = NativeCaptureMailbox()
        var latest: CVPixelBuffer?
        for timestamp in 1...100 {
            var buffer: CVPixelBuffer?
            XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault, 16, 16, kCVPixelFormatType_32BGRA,
                                              nil, &buffer), noErr)
            latest = try XCTUnwrap(buffer)
            XCTAssertEqual(mailbox.replace(latest!, at: Double(timestamp)), timestamp == 1)
        }
        let delivered = try XCTUnwrap(mailbox.take())
        XCTAssertTrue(delivered.0 === latest)
        XCTAssertEqual(delivered.1, 100)
        XCTAssertNil(mailbox.take())
        XCTAssertTrue(mailbox.replace(latest!, at: 101))
        mailbox.close()
        XCTAssertFalse(mailbox.accepting)
        XCTAssertNil(mailbox.take())
        XCTAssertFalse(mailbox.replace(latest!, at: 102))
        XCTAssertNil(mailbox.take())
    }

    func testClosingMailboxRejectsConcurrentCaptureCallbacks() throws {
        var buffer: CVPixelBuffer?
        XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault, 16, 16, kCVPixelFormatType_32BGRA,
                                          nil, &buffer), noErr)
        let sample = try XCTUnwrap(buffer)
        let mailbox = NativeCaptureMailbox()
        DispatchQueue.concurrentPerform(iterations: 1000) { index in
            if index == 500 { mailbox.close() }
            else { _ = mailbox.replace(sample, at: Double(index)) }
        }
        XCTAssertNil(mailbox.take())
        XCTAssertFalse(mailbox.accepting)
    }

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

    func testLosslessImageConversionPreservesOpaqueSourceSamples() throws {
        let width = 64, height = 48
        var buffer: CVPixelBuffer?
        XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA,
            [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &buffer), noErr)
        let source = try XCTUnwrap(buffer)
        CVBufferSetAttachment(source, kCVImageBufferCGColorSpaceKey, CGColorSpace(name: CGColorSpace.sRGB)!, .shouldPropagate)
        CVPixelBufferLockBaseAddress(source, [])
        let stride = CVPixelBufferGetBytesPerRow(source)
        let pointer = try XCTUnwrap(CVPixelBufferGetBaseAddress(source)).assumingMemoryBound(to: UInt8.self)
        var expected = [UInt8](repeating: 0, count: width * height * 4)
        for y in 0..<height {
            for x in 0..<width {
                let pixel: [UInt8] = [UInt8((x * 7) % 256), UInt8((y * 11) % 256), UInt8((x + y) % 256), 255]
                for c in 0..<4 { pointer[y * stride + x * 4 + c] = pixel[c]; expected[(y * width + x) * 4 + c] = pixel[c] }
            }
        }
        CVPixelBufferUnlockBaseAddress(source, [])
        let image = try XCTUnwrap(nativeCaptureImage(source))
        let context = try XCTUnwrap(CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
            bytesPerRow: width * 4, space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGBitmapInfo.byteOrder32Little.rawValue | CGImageAlphaInfo.premultipliedFirst.rawValue))
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        let actual = Data(bytes: try XCTUnwrap(context.data), count: expected.count)
        XCTAssertEqual(actual, Data(expected))
    }

    func testEncoderProbesRequestedPixelsWithoutCapturingTheDisplay() throws {
        XCTAssertNil(NativeVideoEncoder.prepare(width: 0, height: 1080, frameRate: 60, bitrate: 1_000_000))
        XCTAssertNil(NativeVideoEncoder.prepare(width: 8192, height: 8192, frameRate: 60, bitrate: 1_000_000))
        let encoder = try XCTUnwrap(NativeVideoEncoder.prepare(width: 64, height: 64, frameRate: 60, bitrate: 1_000_000))
        VTCompressionSessionInvalidate(encoder.session)
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
