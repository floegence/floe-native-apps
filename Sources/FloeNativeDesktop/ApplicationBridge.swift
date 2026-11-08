import AppKit
import Foundation
import Security

/// Launch Services gives the native app its own privacy responsibility. This
/// relay preserves the consumer's stdio protocol and optional binary media FD.
/// Only the exact launched process may attach; no reusable listening service,
/// network endpoint, command retry or privilege escalation is introduced.
public enum NativeApplicationBridge {
    public enum Failure: Error { case invalidBundle, invalidEndpoint, launchFailed, attachmentFailed; case socketFailure(String, Int32) }
    private static let marker = "--floe-native-application"

    /// Call before initializing capture, accessibility or input machinery.
    /// Returns true only inside the separately launched application process.
    public static func attach(arguments: [String] = CommandLine.arguments) throws -> Bool {
        guard let index = arguments.firstIndex(of: marker) else { return false }
        guard arguments.count == index + 4, let owner = Int32(arguments[index + 2]), owner > 1,
              let media = Int32(arguments[index + 3]), media == -1 || media == 3,
              NativeApplicationAccess.identity() != nil else { throw Failure.invalidEndpoint }
        NSApplication.shared.setActivationPolicy(.accessory)
        NSApplication.shared.finishLaunching()
        let root = arguments[index + 1]
        var info = stat()
        guard root.hasPrefix("/private/tmp/floe-native-"), UUID(uuidString: String(root.dropFirst("/private/tmp/floe-native-".count))) != nil, lstat(root, &info) == 0,
              info.st_mode & S_IFMT == S_IFDIR, info.st_uid == getuid(), info.st_mode & 0o777 == 0o700 else {
            throw Failure.invalidEndpoint
        }
        let control = try connect(root + "/control", owner: owner)
        defer { if control > 3 || (control == 3 && media != 3) { close(control) } }
        let mediaSocket = media == 3 ? try connect(root + "/media", owner: owner) : -1
        // Both peers are authenticated. Unlink now so an uncatchable relay
        // termination cannot leave endpoints behind. Existing sockets survive.
        _ = unlink(root + "/control")
        if media == 3 { _ = unlink(root + "/media") }
        _ = rmdir(root)
        // Duplicate media first: descriptor 3 may itself be the control socket.
        let savedControl = fcntl(control, F_DUPFD_CLOEXEC, 10)
        guard savedControl >= 0 else { throw Failure.socketFailure(#function, errno) }
        defer { close(savedControl) }
        if mediaSocket >= 0 {
            guard dup2(mediaSocket, 3) == 3 else { throw Failure.socketFailure(#function, errno) }
            if mediaSocket != 3 { close(mediaSocket) }
            setenv("FLOE_NATIVE_MEDIA_FD", "3", 1)
        }
        guard dup2(savedControl, STDIN_FILENO) == STDIN_FILENO,
              dup2(savedControl, STDOUT_FILENO) == STDOUT_FILENO else { throw Failure.socketFailure(#function, errno) }
        return true
    }

    /// Must run on the main thread. The caller exits with the returned status.
    public static func relay(bundle: URL, arguments: [String], mediaFD: Int32? = nil, environment: [String: String] = [:]) throws -> Int32 {
        guard Thread.isMainThread, mediaFD == nil || mediaFD == 3, !arguments.contains(marker) else { throw Failure.invalidEndpoint }
        let bundle = bundle.resolvingSymlinksInPath().standardizedFileURL
        try validate(bundle: bundle)
        let root = "/private/tmp/floe-native-" + UUID().uuidString
        guard mkdir(root, 0o700) == 0 else { throw Failure.socketFailure(#function, errno) }
        defer { try? FileManager.default.removeItem(atPath: root) }
        let controlListener = try listen(root + "/control")
        defer { close(controlListener) }
        let mediaListener = mediaFD != nil ? try listen(root + "/media") : -1
        defer { if mediaListener >= 0 { close(mediaListener) } }
        let config = NSWorkspace.OpenConfiguration()
        config.activates = false
        config.addsToRecentItems = false
        config.createsNewApplicationInstance = true
        config.allowsRunningApplicationSubstitution = false
        config.arguments = arguments + [marker, root, String(getpid()), mediaFD == nil ? "-1" : "3"]
        // Do not inherit terminal/loader or Electron variables into the
        // permission owner. Only explicit consumer values and the media descriptor are propagated.
        guard environment["FLOE_NATIVE_MEDIA_FD"] == nil else { throw Failure.invalidEndpoint }
        config.environment = environment.merging(mediaFD == nil ? [:] : ["FLOE_NATIVE_MEDIA_FD": "3"]) { _, value in value }
        var application: NSRunningApplication?
        var completed = false
        var result: Int32 = 1
        var signals: [DispatchSourceSignal] = []
        for number in [SIGTERM, SIGINT, SIGHUP] {
            signal(number, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
            source.setEventHandler { application?.terminate(); result = 128 + number; completed = true }
            source.resume(); signals.append(source)
        }
        defer {
            signals.forEach { $0.cancel() }
            if let application, !application.isTerminated { application.forceTerminate() }
        }
        NSWorkspace.shared.openApplication(at: bundle, configuration: config) { app, error in
            DispatchQueue.main.async {
            guard !completed, error == nil, let app, app.bundleURL?.resolvingSymlinksInPath().standardizedFileURL == bundle else {
                FileHandle.standardError.write(Data("Application launch failed: \(String(describing: error)) \(String(describing: app?.bundleURL))\n".utf8)); app?.terminate(); completed = true; return
            }
            application = app
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    let control = try accept(controlListener, owner: app.processIdentifier)
                    defer { close(control) }
                    let media = mediaListener >= 0 ? try accept(mediaListener, owner: app.processIdentifier) : -1
                    let group = DispatchGroup()
                    if media >= 0, let mediaFD {
                        group.enter()
                        DispatchQueue.global(qos: .userInitiated).async {
                            copy(from: media, to: mediaFD); close(media); group.leave()
                        }
                    }
                    DispatchQueue.global(qos: .userInitiated).async {
                        copy(from: STDIN_FILENO, to: control)
                        _ = shutdown(control, SHUT_WR)
                    }
                    copy(from: control, to: STDOUT_FILENO)
                    _ = shutdown(control, SHUT_RDWR)
                    if media >= 0 { _ = shutdown(media, SHUT_RD) }
                    group.wait()
                      DispatchQueue.main.async { result = 0; completed = true }
                } catch { FileHandle.standardError.write(Data("Application attachment failed: \(error)\n".utf8)); DispatchQueue.main.async { completed = true } }
            }
            }
        }
        let startupDeadline = Date().addingTimeInterval(25)
        while !completed {
            RunLoop.current.run(until: Date().addingTimeInterval(0.02))
            if application == nil && Date() > startupDeadline { completed = true }
        }
        return result
    }

    public static func validate(bundle: URL) throws {
        guard bundle.isFileURL, let metadata = Bundle(url: bundle),
              NativeApplicationAccess.identity(bundle: metadata) != nil else { throw Failure.invalidBundle }
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(bundle as CFURL, [], &code) == errSecSuccess, let code,
              SecStaticCodeCheckValidity(code, SecCSFlags(rawValue: kSecCSCheckAllArchitectures | kSecCSStrictValidate), nil) == errSecSuccess else { throw Failure.invalidBundle }
        var information: CFDictionary?
        guard SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &information) == errSecSuccess,
              let values = information as? [String: Any],
              let flags = values[kSecCodeInfoFlags as String] as? UInt32, flags & 2 == 0 else { throw Failure.invalidBundle }
    }

    private static func address(_ path: String) throws -> sockaddr_un {
        var value = sockaddr_un()
        let bytes = Array(path.utf8) + [0]
        guard bytes.count <= MemoryLayout.size(ofValue: value.sun_path) else { throw Failure.invalidEndpoint }
        value.sun_family = sa_family_t(AF_UNIX)
        value.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        withUnsafeMutableBytes(of: &value.sun_path) { target in target.copyBytes(from: bytes) }
        return value
    }
    private static func socket() throws -> Int32 {
        let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw Failure.socketFailure(#function, errno) }
        var one: Int32 = 1
        _ = setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout.size(ofValue: one)))
        _ = fcntl(descriptor, F_SETFD, FD_CLOEXEC)
        return descriptor
    }
    private static func listen(_ path: String) throws -> Int32 {
        let fd = try socket()
        var value = try address(path)
        let bound = withUnsafePointer(to: &value) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard bound == 0, chmod(path, 0o600) == 0, Darwin.listen(fd, 2) == 0 else { close(fd); throw Failure.socketFailure(#function, errno) }
        return fd
    }
    private static func verifyPeer(_ fd: Int32, owner: pid_t) throws {
        var uid: uid_t = 0, gid: gid_t = 0, pid: pid_t = 0
        var size = socklen_t(MemoryLayout.size(ofValue: pid))
        guard getpeereid(fd, &uid, &gid) == 0, uid == getuid(),
              getsockopt(fd, SOL_LOCAL, LOCAL_PEERPID, &pid, &size) == 0, pid == owner else { throw Failure.attachmentFailed }
    }
    private static func connect(_ path: String, owner: pid_t) throws -> Int32 {
        let fd = try socket()
        do {
            var value = try address(path)
            let connected = withUnsafePointer(to: &value) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
            }
            guard connected == 0 else { throw Failure.socketFailure(#function, errno) }
            try verifyPeer(fd, owner: owner); return fd
        } catch { close(fd); throw error }
    }
    private static func accept(_ listener: Int32, owner: pid_t) throws -> Int32 {
        var pollFD = pollfd(fd: listener, events: Int16(POLLIN), revents: 0)
        guard poll(&pollFD, 1, 20000) > 0 else { throw Failure.attachmentFailed }
        let fd = Darwin.accept(listener, nil, nil)
        guard fd >= 0 else { throw Failure.socketFailure(#function, errno) }
        do {
            var one: Int32 = 1
            guard setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, socklen_t(MemoryLayout.size(ofValue: one))) == 0,
                  fcntl(fd, F_SETFD, FD_CLOEXEC) == 0 else { throw Failure.socketFailure(#function, errno) }
            try verifyPeer(fd, owner: owner); return fd
        }
        catch { close(fd); throw error }
    }
    private static func copy(from source: Int32, to destination: Int32) {
        var buffer = [UInt8](repeating: 0, count: 65536)
        while true {
            let count = Darwin.read(source, &buffer, buffer.count)
            if count < 0 && errno == EINTR { continue }
            guard count > 0 else { return }
            var offset = 0
            while offset < count {
                let written = buffer.withUnsafeBytes { Darwin.write(destination, $0.baseAddress!.advanced(by: offset), count - offset) }
                if written < 0 && errno == EINTR { continue }
                guard written > 0 else { return }
                offset += written
            }
        }
    }
}
