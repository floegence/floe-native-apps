import AppKit
import FloeNativeDesktop

do {
    if try NativeApplicationBridge.attach() {
        guard let identity = NativeApplicationAccess.identity() else { exit(2) }
        let data = try JSONSerialization.data(withJSONObject: ["pid": getpid(), "parent": getppid(),
            "name": identity.name, "identifier": identity.identifier, "path": identity.url.path, "endpoint": CommandLine.arguments[CommandLine.arguments.count - 3]])
        FileHandle.standardOutput.write(data + Data([10]))
        if ProcessInfo.processInfo.environment["FLOE_NATIVE_MEDIA_FD"] == "3" {
            FileHandle(fileDescriptor: 3, closeOnDealloc: false).write(Data("media-fixture".utf8))
        }
        DispatchQueue.global().async {
            while let line = readLine() { print(line); fflush(stdout) }
            exit(0)
        }
        RunLoop.main.run()
    } else {
        guard (2...3).contains(CommandLine.arguments.count) else { exit(2) }
        exit(try NativeApplicationBridge.relay(bundle: URL(fileURLWithPath: CommandLine.arguments[1]), arguments: [],
            mediaFD: CommandLine.arguments.last == "--media" ? 3 : nil))
    }
} catch {
    FileHandle.standardError.write(Data("Native application qualification failed: \(error)\n".utf8)); exit(1)
}
