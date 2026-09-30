import AppKit
import FloeNativeDesktop

// A task-owned native fixture. Probe performs no capture or input. Session mode
// uses the same public engine as a consumer and never changes OS permissions.
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let writer = NSLock()
func output(_ message: [String: Any]) {
    guard let bytes = try? JSONSerialization.data(withJSONObject: message, options: [.sortedKeys]) else { return }
    writer.lock(); defer { writer.unlock() }
    FileHandle.standardOutput.write(bytes)
    FileHandle.standardOutput.write(Data([10]))
}
if CommandLine.arguments.dropFirst().first == "probe" {
    output(NativeDesktopSession.capabilities())
} else if CommandLine.arguments.dropFirst().first == "session" {
    let session = NativeDesktopSession(mayControl: { true }, output: output)
    DispatchQueue.global(qos: .userInitiated).async {
        while let line = readLine(strippingNewline: true) {
            guard line.utf8.count <= 8 << 20, let bytes = line.data(using: .utf8),
                  let request = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any] else { continue }
            DispatchQueue.main.async { session.handle(request) }
        }
        DispatchQueue.main.async { session.close { application.terminate(nil) } }
    }
    application.run()
} else {
    FileHandle.standardError.write(Data("Usage: NativeDesktopQualification probe|session\n".utf8))
    exit(2)
}
