import XCTest
@testable import FloeNativeDesktop

final class ApplicationAccessTests: XCTestCase {
    func testRequestDoesNotOpenSettingsWhileThePromptIsPending() throws {
        var calls: [String] = []
        try NativeApplicationAccess.perform("accessibility", requestScreen: { calls.append("screen") },
            requestInput: { calls.append("input") }, openSettings: { calls.append($0) }, reveal: { calls.append("reveal") })
        XCTAssertEqual(calls, ["input"])
    }
    func testSettingsAndRevealAreExplicitOperations() throws {
        var calls: [String] = []
        for operation in ["screen_recording_settings", "accessibility_settings", "reveal_application"] {
            try NativeApplicationAccess.perform(operation, requestScreen: { XCTFail() }, requestInput: { XCTFail() },
                openSettings: { calls.append($0) }, reveal: { calls.append("reveal") })
        }
        XCTAssertEqual(calls, ["Privacy_ScreenCapture", "Privacy_Accessibility", "reveal"])
    }
    func testUnknownOperationsHaveNoEffects() {
        XCTAssertThrowsError(try NativeApplicationAccess.perform("reset_all", requestScreen: { XCTFail() },
            requestInput: { XCTFail() }, openSettings: { _ in XCTFail() }, reveal: { XCTFail() }))
    }
    func testOnlyARealApplicationBundleSuppliesPermissionIdentity() {
        XCTAssertNil(NativeApplicationAccess.identity(bundle: .main))
    }
    func testBridgeRejectsNonApplicationAndMalformedAttachment() {
        XCTAssertThrowsError(try NativeApplicationBridge.validate(bundle: URL(fileURLWithPath: "/bin/cat")))
        XCTAssertFalse(try NativeApplicationBridge.attach(arguments: ["fixture"]))
        XCTAssertThrowsError(try NativeApplicationBridge.attach(arguments: ["fixture", "--floe-native-application"]))
        XCTAssertThrowsError(try NativeApplicationBridge.attach(arguments: ["fixture", "--floe-native-application", "/tmp/fake", "1", "3"]))
    }
}
