import AppKit
import ApplicationServices
import ScreenCaptureKit

// A consumer-authorized physical desktop attachment. It owns only native
// capture and input; the consumer owns authentication and the console lease.
public final class NativeDesktopSession {
    public typealias Output = ([String: Any]) -> Void
    private let output: Output
    private let mayControl: () -> Bool
    private var capture: NativeCaptureStream?
    private var generation = 0
    private var sentFrames: Set<Int> = []
    private var painted = false
    private var displayID: CGDirectDisplayID?
    private var desiredDisplay: CGDirectDisplayID?
    private var mode = "view"
    private var picture: NativeCaptureSettings?
    private var transition = false
    private var closed = false
    private var closeCallbacks: [() -> Void] = []
    private var state = "disconnected"
    private var keys: [CGKeyCode: CGEventFlags] = [:]
    private var buttons: Set<CGMouseButton> = []
    private var lastPoint = CGPoint.zero
    private var timer: Timer?
    private var displaySnapshot: [String] = []
    private var clipboardChange = -1
    private var lastClipboard: String?
    private var clipboardSync = false
    private var lockRequestedAt: TimeInterval?
    private let deliveryLock = NSLock()
    private var pendingAudio = 0

    public init(mayControl: @escaping () -> Bool, output: @escaping Output) {
        self.mayControl = mayControl; self.output = output
    }

    private static var consoleReadiness: String {
        guard let session = CGSessionCopyCurrentDictionary() as? [String: Any],
              session[kCGSessionOnConsoleKey as String] as? Bool == true else { return "session_unavailable" }
        if session["CGSSessionScreenIsLocked"] as? Bool == true { return "locked" }
        return "ready"
    }

    public static var readiness: String {
        let console = consoleReadiness
        if console != "ready" { return console }
        if !CGPreflightScreenCaptureAccess() { return "screen_permission_required" }
        return "ready"
    }

    private static func displayIDs() -> [CGDirectDisplayID] {
        var count: UInt32 = 0
        guard CGGetActiveDisplayList(0, nil, &count) == .success else { return [] }
        var displays = [CGDirectDisplayID](repeating: 0, count: Int(count))
        guard CGGetActiveDisplayList(count, &displays, &count) == .success else { return [] }
        return Array(displays.prefix(Int(count)))
    }

    public static func displays() -> [[String: Any]] {
        displayIDs().map { id in
            let bounds = CGDisplayBounds(id)
            let screen = NSScreen.screens.first { ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == id }
            return ["id": String(id), "name": screen?.localizedName ?? "", "x": Int(bounds.minX), "y": Int(bounds.minY),
                    "width": Int(bounds.width), "height": Int(bounds.height),
                    "scale": Double(CGDisplayCopyDisplayMode(id)?.pixelWidth ?? CGDisplayPixelsWide(id)) / max(1, bounds.width), "primary": id == CGMainDisplayID()]
        }
    }

    private static func displaySignature() -> [String] {
        displayIDs().map { id in
            let bounds = CGDisplayBounds(id)
            let mode = CGDisplayCopyDisplayMode(id)
            return "\(id):\(bounds):\(mode?.pixelWidth ?? 0):\(mode?.pixelHeight ?? 0)"
        }
    }

    public static func capabilities() -> [String: Any] {
        let state = readiness
        return ["backend": "macos", "state": state, "screen": CGPreflightScreenCaptureAccess(),
                "input": AXIsProcessTrusted(), "clipboard": true, "audio": NativeOpusEncoder.isSupported,
                "unattended": true, "unlock": false, "encoder": "videotoolbox", "displays": displays()]
    }

    private func emit(_ message: [String: Any]) {
        var envelope = message
        envelope["version"] = 1
        output(envelope)
    }

    public func handle(_ request: [String: Any]) {
        dispatchPrecondition(condition: .onQueue(.main))
        let id = request["id"] as? Int ?? 0
        do {
            guard !closed, request["version"] as? Int == 1, id > 0,
                  let method = request["method"] as? String else { throw failure("INVALID_ARGUMENT") }
            switch method {
            case "probe":
                emit(["type": "capabilities", "id": id, "capabilities": Self.capabilities()]); return
            case "connect":
                guard capture == nil, !transition, state == "disconnected",
                      let selectedMode = request["mode"] as? String, ["view", "control"].contains(selectedMode) else { throw failure("SESSION_ALREADY_STARTED") }
                mode = selectedMode
                picture = try settings(request["picture"])
                let candidate = (request["display_id"] as? String).flatMap(UInt32.init) ?? CGMainDisplayID()
                guard Self.displayIDs().contains(candidate) else { throw failure("DISPLAY_UNAVAILABLE") }
                desiredDisplay = candidate
                if mode == "control", !AXIsProcessTrusted() { throw failure("INPUT_PERMISSION_REQUIRED") }
                if mode == "control", !mayControl() { throw failure("CONTROL_UNAVAILABLE") }
                guard Self.readiness == "ready" else { throw failure(Self.readiness.uppercased()) }
                timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in self?.observe() }
                displaySnapshot = Self.displaySignature()
                replaceCapture()
            case "disconnect":
                close(); return
            case "frame_ack":
                try validateGeneration(request)
                guard let frame = request["frame_id"] as? Int, sentFrames.contains(frame) else { throw failure("INVALID_FRAME") }
                sentFrames = sentFrames.filter { $0 > frame }
                painted = true; capture?.acknowledge(frame)
            case "set_mode":
                try validateGeneration(request)
                guard let next = request["mode"] as? String, ["view", "control"].contains(next) else { throw failure("INVALID_ARGUMENT") }
                if next == "control", !mayControl() || !AXIsProcessTrusted() { throw failure("CONTROL_UNAVAILABLE") }
                releaseInput(); mode = next; replaceCapture()
            case "select_display":
                try validateGeneration(request)
                guard let raw = request["display_id"] as? String, let next = UInt32(raw), Self.displayIDs().contains(next) else { throw failure("DISPLAY_UNAVAILABLE") }
                desiredDisplay = next; replaceCapture()
            case "configure":
                try validateGeneration(request)
                let next = try settings(request["picture"])
                if picture != next { picture = next; replaceCapture() }
            case "keyframe":
                try validateGeneration(request); replaceCapture()
            case "release_input":
                try validateGeneration(request); releaseInput()
            case "input":
                try authorizeInput(request)
                guard let input = request["input"] as? [String: Any] else { throw failure("INVALID_ARGUMENT") }
                try deliver(input)
            case "get_clipboard":
                try authorizeInput(request)
                let text = NSPasteboard.general.string(forType: .string) ?? ""
                guard text.utf8.count <= 1 << 20 else { throw failure("CLIPBOARD_TOO_LARGE") }
                emit(["type": "clipboard", "id": id, "generation": generation, "text": text]); return
            case "set_clipboard_sync":
                try validateGeneration(request)
                guard let enabled = request["enabled"] as? Bool else { throw failure("INVALID_ARGUMENT") }
                if enabled { try authorizeInput(request) }
                clipboardSync = enabled
            case "set_clipboard":
                try authorizeInput(request)
                guard let text = request["text"] as? String, text.utf8.count <= 1 << 20, !text.contains("\0") else { throw failure("INVALID_ARGUMENT") }
                NSPasteboard.general.clearContents()
                guard NSPasteboard.general.setString(text, forType: .string) else { throw failure("CLIPBOARD_UNAVAILABLE") }
                lastClipboard = text; clipboardChange = NSPasteboard.general.changeCount
            case "lock":
                try authorizeInput(request)
                releaseInput()
                for event in try NativeDesktopInput.key("ctrl+cmd+q") { event.post(tap: .cghidEventTap) }
                lockRequestedAt = ProcessInfo.processInfo.systemUptime
                suspend("locking")
            default: throw failure("METHOD_UNSUPPORTED")
            }
            emit(["type": "result", "id": id, "generation": generation])
        } catch let error as NativeDesktopError {
            emit(["type": "error", "id": id, "code": error.code, "generation": generation])
        } catch {
            emit(["type": "error", "id": id, "code": "NATIVE_DESKTOP_FAILED", "generation": generation])
        }
    }

    private func failure(_ code: String) -> NativeDesktopError { NativeDesktopError(code: code, message: code) }

    private func settings(_ value: Any?) throws -> NativeCaptureSettings {
        guard var object = value as? [String: Any] else { throw failure("INVALID_ARGUMENT") }
        object["video"] = true; object["require_video"] = true; object["frame_capacity"] = 4; object["pixel_ratio"] = 4
        return try NativeCaptureSettings(request: object)
    }

    private func validateGeneration(_ request: [String: Any]) throws {
        guard request["generation"] as? Int == generation, state == "active", !transition else { throw failure("STALE_DESKTOP") }
    }

    private func authorizeInput(_ request: [String: Any]) throws {
        try validateGeneration(request)
        guard Self.readiness == "ready", AXIsProcessTrusted() else { observe(); throw failure("PERMISSION_REQUIRED") }
        guard Self.displaySignature() == displaySnapshot else { observe(); throw failure("DISPLAY_CHANGED") }
        guard painted else { throw failure("DESKTOP_NOT_PAINTED") }
        guard mode == "control", mayControl() else { throw failure("VIEW_ONLY") }
    }

    private func replaceCapture() {
        guard !closed else { return }
        generation += 1; painted = false; sentFrames.removeAll(); releaseInput()
        state = "connecting"; emitState()
        advanceCapture()
    }

    // Exactly one asynchronous native start or stop owns lifecycle progress.
    // Revocation changes desired state immediately; that operation must retire
    // before its successor can begin, including a start still awaiting consent.
    private func advanceCapture() {
        guard !transition else { return }
        if let previous = capture {
            transition = true
            previous.stop { [self] in
                capture = nil; transition = false
                advanceCapture()
            }
        } else if !closed, state == "connecting" {
            beginCapture()
        } else if closed {
            let callbacks = closeCallbacks; closeCallbacks.removeAll()
            for callback in callbacks { callback() }
        }
    }

    private func beginCapture() {
        guard !closed, state == "connecting", let target = desiredDisplay, let picture else { return }
        transition = true
        let expected = generation
        SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { [self] content, error in
            DispatchQueue.main.async {
                guard !self.closed, expected == self.generation, self.state == "connecting" else {
                    self.transition = false; self.advanceCapture(); return
                }
                guard error == nil, Self.readiness == "ready",
                      let display = content?.displays.first(where: { $0.displayID == target }) else {
                    self.transition = false; self.state = Self.readiness == "ready" ? "capture_unavailable" : Self.readiness
                    self.emitState(); return
                }
                self.displayID = target
                let stream = NativeCaptureStream(generation: expected, settings: picture, output: { [weak self] message in
                    guard let self else { return }
                    let audio = message["type"] as? String == "audio"
                    if audio {
                        self.deliveryLock.lock()
                        let admitted = self.pendingAudio < 4
                        if admitted { self.pendingAudio += 1 }
                        self.deliveryLock.unlock()
                        if !admitted { return }
                    }
                    DispatchQueue.main.async {
                        defer {
                            if audio {
                                self.deliveryLock.lock(); self.pendingAudio -= 1; self.deliveryLock.unlock()
                            }
                        }
                        // ScreenCaptureKit owns authorization for the samples it
                        // supplies and reports revocation through stream failure.
                        // Check console identity here; input admission independently
                        // checks current capture permission before posting events.
                        guard !self.closed, self.generation == expected, Self.consoleReadiness == "ready" else { return }
                        var packet = message
                        packet["display_id"] = String(target)
                        if let frame = message["frame_id"] as? Int { self.sentFrames.insert(frame) }
                        self.emit(packet)
                    }
                }, failed: { [weak self] _ in
                    guard let self, self.generation == expected else { return }
                    self.suspend("capture_unavailable")
                })
                self.capture = stream
                stream.start(display) {
                    self.transition = false
                    if self.closed || expected != self.generation || self.state != "connecting" {
                        self.advanceCapture()
                    } else if self.state == "connecting" {
                        self.state = "active"; self.emitState()
                    }
                }
            }
        }
    }

    private func emitState() {
        emit(["type": "state", "state": state, "mode": mode, "generation": generation,
              "display_id": displayID.map(String.init) ?? "", "displays": Self.displays()])
    }

    private func suspend(_ reason: String) {
        guard state != reason else { return }
        generation += 1; painted = false; sentFrames.removeAll(); releaseInput()
        state = reason
        emitState()
        advanceCapture()
    }

    private func observe() {
        guard !closed else { return }
        let ready = Self.readiness
        if ready != "ready" { suspend(ready); return }
        if state == "locking" {
            if ProcessInfo.processInfo.systemUptime - (lockRequestedAt ?? 0) >= 2 { suspend("lock_not_confirmed") }
            return
        }
        if mode == "control", !AXIsProcessTrusted() || !mayControl() {
            revokeControl(); return
        }
        let displays = Self.displayIDs()
        let signature = Self.displaySignature()
        if signature != displaySnapshot {
            displaySnapshot = signature
            if let desiredDisplay, !displays.contains(desiredDisplay) { self.desiredDisplay = displays.first }
            if desiredDisplay == nil { suspend("display_unavailable") }
            else { replaceCapture() }
            return
        }
        if ["locked", "session_unavailable", "screen_permission_required"].contains(state) {
            replaceCapture(); return
        }
        guard clipboardSync, painted, mode == "control", mayControl(), state == "active" else { return }
        let pasteboard = NSPasteboard.general
        if clipboardChange != pasteboard.changeCount {
            clipboardChange = pasteboard.changeCount
            if let text = pasteboard.string(forType: .string), text.utf8.count <= 1 << 20, text != lastClipboard {
                lastClipboard = text
                emit(["type": "clipboard", "generation": generation, "text": text])
            }
        }
    }

    private func deliver(_ input: [String: Any]) throws {
        guard let kind = input["kind"] as? String, let displayID else { throw failure("INVALID_ARGUMENT") }
        var events: [CGEvent] = []
        if kind == "text" {
            guard let text = input["text"] as? String, text.utf8.count <= 16000, !text.contains("\0") else { throw failure("INVALID_ARGUMENT") }
            events = try NativeDesktopInput.text(text)
        } else if kind == "key" {
            var key = input
            if (key["key"] as? String ?? "").isEmpty { key["key"] = key["code"] }
            for name in ["pressed", "repeat", "shiftKey", "ctrlKey", "altKey", "metaKey"] { if key[name] == nil { key[name] = false } }
            let event = try NativeDesktopInput.viewerKey(key, physical: true)
            let code = CGKeyCode(event.getIntegerValueField(.keyboardEventKeycode))
            if key["pressed"] as? Bool != true, keys[code] == nil { return }
            if key["pressed"] as? Bool == true { keys[code] = event.flags } else { keys.removeValue(forKey: code) }
            events = [event]
        } else {
            func number(_ name: String, _ range: ClosedRange<Double>) throws -> Double {
                guard let value = input[name] as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(), value.doubleValue.isFinite, range.contains(value.doubleValue) else { throw failure("INVALID_ARGUMENT") }
                return value.doubleValue
            }
            let bounds = CGDisplayBounds(displayID)
            let point = CGPoint(x: bounds.minX + (try number("x", 0...1)) * max(0, bounds.width - 1),
                                y: bounds.minY + (try number("y", 0...1)) * max(0, bounds.height - 1))
            lastPoint = point
            if kind == "scroll" {
                events = try NativeDesktopInput.scroll(at: point, x: number("dx", -10000...10000), y: number("dy", -10000...10000),
                    naturalScrolling: UserDefaults.standard.bool(forKey: "com.apple.swipescrolldirection"))
            } else {
                let raw = input["button"] as? Int ?? 0
                guard (0...2).contains(raw) else { throw failure("INVALID_ARGUMENT") }
                let button: CGMouseButton = raw == 2 ? .right : raw == 1 ? .center : .left
                let type: CGEventType
                switch kind {
                case "move": type = buttons.contains(.left) ? .leftMouseDragged : buttons.contains(.right) ? .rightMouseDragged : buttons.contains(.center) ? .otherMouseDragged : .mouseMoved
                case "down": type = button == .left ? .leftMouseDown : button == .right ? .rightMouseDown : .otherMouseDown; buttons.insert(button)
                case "up":
                    guard buttons.contains(button) else { return }
                    type = button == .left ? .leftMouseUp : button == .right ? .rightMouseUp : .otherMouseUp; buttons.remove(button)
                default: throw failure("INVALID_ARGUMENT")
                }
                guard let event = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: button) else { throw failure("INPUT_UNAVAILABLE") }
                if input["shiftKey"] as? Bool == true { event.flags.insert(.maskShift) }
                if input["ctrlKey"] as? Bool == true { event.flags.insert(.maskControl) }
                if input["altKey"] as? Bool == true { event.flags.insert(.maskAlternate) }
                if input["metaKey"] as? Bool == true { event.flags.insert(.maskCommand) }
                event.setIntegerValueField(.mouseEventClickState, value: Int64(min(3, max(1, input["clicks"] as? Int ?? 1))))
                events = [event]
            }
        }
        for event in events {
            // Screen authorization is checked once when admitting this request.
            // Recheck login, lock, input permission and ownership for every event,
            // including a multi-event text commit, without repeating TCC capture IPC.
            guard Self.consoleReadiness == "ready", AXIsProcessTrusted(), mayControl() else { releaseInput(); throw failure("CONTROL_REVOKED") }
            event.post(tap: .cghidEventTap)
        }
    }

    public func releaseInput() {
        for (code, _) in keys {
            if let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false) {
                if [54, 55, 56, 57, 58, 59, 60, 61, 62].contains(code) { event.type = .flagsChanged }
                event.flags = []; event.post(tap: .cghidEventTap)
            }
        }
        keys.removeAll()
        for button in buttons {
            let type: CGEventType = button == .left ? .leftMouseUp : button == .right ? .rightMouseUp : .otherMouseUp
            CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: lastPoint, mouseButton: button)?.post(tap: .cghidEventTap)
        }
        buttons.removeAll()
    }

    public func revokeControl() {
        dispatchPrecondition(condition: .onQueue(.main))
        releaseInput()
        guard mode == "control" else { return }
        mode = "view"; clipboardSync = false
        if state == "active" || state == "connecting" { replaceCapture() }
        else { emitState() }
    }

    public func close(completion: @escaping () -> Void = {}) {
        dispatchPrecondition(condition: .onQueue(.main))
        closeCallbacks.append(completion)
        if closed { advanceCapture(); return }
        closed = true; generation += 1; painted = false; sentFrames.removeAll()
        clipboardSync = false
        timer?.invalidate(); timer = nil; releaseInput()
        state = "disconnected"; emitState()
        advanceCapture()
    }
}
