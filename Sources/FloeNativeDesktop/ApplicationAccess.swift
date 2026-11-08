import AppKit
import ApplicationServices
import Security

/// Permission presentation comes from the running application, never its parent
/// environment. Consumers own user consent and feature enablement.
public enum NativeApplicationAccess {
    public struct Identity {
        public let name: String
        public let identifier: String
        public let url: URL
    }

    public enum Failure: Error { case unsupportedOperation, applicationRequired, settingsUnavailable }

    public static func identity(bundle: Bundle = .main) -> Identity? {
        guard bundle.bundleURL.pathExtension == "app",
              bundle.object(forInfoDictionaryKey: "CFBundlePackageType") as? String == "APPL",
              let identifier = bundle.bundleIdentifier, !identifier.isEmpty,
              let name = bundle.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String
                ?? bundle.object(forInfoDictionaryKey: "CFBundleName") as? String, !name.isEmpty else { return nil }
        return Identity(name: name, identifier: identifier, url: bundle.bundleURL)
    }

    public static func perform(_ operation: String) throws {
        guard let application = identity() else { throw Failure.applicationRequired }
        try perform(operation, requestScreen: { _ = CGRequestScreenCaptureAccess() },
            requestInput: {
                _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
            }, openSettings: { pane in
                guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?" + pane),
                      NSWorkspace.shared.open(url) else { throw Failure.settingsUnavailable }
            }, reveal: { NSWorkspace.shared.activateFileViewerSelecting([application.url]) })
    }

    // A request's return value is not the asynchronous user's decision. Do not
    // race the system prompt by opening settings immediately afterwards.
    static func perform(_ operation: String, requestScreen: () throws -> Void,
                        requestInput: () throws -> Void, openSettings: (String) throws -> Void,
                        reveal: () throws -> Void) throws {
        switch operation {
        case "screen_recording": try requestScreen()
        case "accessibility": try requestInput()
        case "screen_recording_settings": try openSettings("Privacy_ScreenCapture")
        case "accessibility_settings": try openSettings("Privacy_Accessibility")
        case "reveal_application": try reveal()
        default: throw Failure.unsupportedOperation
        }
    }
}
