import AppKit
import FloeNativeDesktop

// Separate receiver process: a synchronous sender must not delay the AppKit
// callbacks used to measure delivery. Only this disposable window receives input.
final class GlobalInputProbe {
    var ownEvents = 0, userEvents = 0
    var tap: CFMachPort?
    var source: CFRunLoopSource?
    init() {
        let mask = [CGEventType.mouseMoved, .leftMouseDown, .leftMouseDragged, .leftMouseUp].reduce(CGEventMask(0)) { $0 | (1 << $1.rawValue) }
        tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly, eventsOfInterest: mask, callback: { _, _, event, pointer in
            let owner = Unmanaged<GlobalInputProbe>.fromOpaque(pointer!).takeUnretainedValue()
            if event.getIntegerValueField(.eventSourceUnixProcessID) == Int64(getpid()) { owner.ownEvents += 1 } else { owner.userEvents += 1 }
            return Unmanaged.passUnretained(event)
        }, userInfo: Unmanaged.passUnretained(self).toOpaque())
        if let tap { source = CFMachPortCreateRunLoopSource(nil,tap,0); CFRunLoopAddSource(CFRunLoopGetMain(),source,.commonModes); CGEvent.tapEnable(tap:tap,enable:true) }
    }
    deinit { if let tap { CFMachPortInvalidate(tap) }; if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(),source,.commonModes) } }
}
let app = NSApplication.shared
func output(_ value: [String: Any]) {
    let body = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    FileHandle.standardOutput.write(body); FileHandle.standardOutput.write(Data([10]))
}
final class DragWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}
final class DragPad: NSView {
    var received: [[String: Any]] = []
    var save: (() -> Void)?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    func record(_ event: NSEvent) {
        received.append(["type": event.type == .leftMouseDown ? "down" : event.type == .leftMouseUp ? "up" : "drag",
                         "time_ms": ProcessInfo.processInfo.systemUptime * 1000, "event_time_ms": event.timestamp * 1000,
                         "x": Double(event.locationInWindow.x), "y": Double(event.locationInWindow.y)])
        save?()
    }
    override func mouseDown(with event: NSEvent) { record(event) }
    override func mouseDragged(with event: NSEvent) { record(event) }
    override func mouseUp(with event: NSEvent) { record(event) }
}
if CommandLine.arguments.contains("--fixture") {
    let path = CommandLine.arguments.last!
    app.setActivationPolicy(.regular)
    app.finishLaunching()
    let window = DragWindow(contentRect: NSRect(x: 200, y: 200, width: 400, height: 250), styleMask: [.titled], backing: .buffered, defer: false)
    window.title = "Floe native drag qualification"
    window.animationBehavior = .none
    let pad = DragPad(frame: NSRect(x: 0, y: 0, width: 400, height: 250))
    pad.wantsLayer = true; pad.layer?.backgroundColor = NSColor.systemTeal.cgColor
    window.contentView = pad
    pad.save = {
        let start = window.convertPoint(toScreen: NSPoint(x: 50, y: 100))
        let end = window.convertPoint(toScreen: NSPoint(x: 300, y: 150))
        let height = NSScreen.screens[0].frame.maxY
        let value: [String: Any] = ["events": pad.received, "window_id":window.windowNumber, "ready": window.isKeyWindow,
            "start_x": start.x, "start_y": height - start.y, "end_x": end.x, "end_y": height - end.y]
        try! JSONSerialization.data(withJSONObject: value).write(to: URL(fileURLWithPath: path), options: .atomic)
    }
    let observer = NotificationCenter.default.addObserver(forName: NSWindow.didBecomeKeyNotification, object: window, queue: .main) { _ in pad.save?() }
    DispatchQueue.main.async {
        window.makeKeyAndOrderFront(nil); app.activate(ignoringOtherApps: true); pad.save?()
    }
    app.run()
    NotificationCenter.default.removeObserver(observer)
} else {
    let original = NSWorkspace.shared.frontmostApplication
    let pointer = CGEvent(source: nil)?.location
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    var failures = 0
    let legacy = CommandLine.arguments.contains("--legacy")
    let directed = CommandLine.arguments.contains("--directed")
    let cases = legacy ? [(500, false)] : [(0, false), (1, false), (500, false), (5000, false), (5000, true)]
    enum Stopped: Error { case cancelled, targetChanged, fixtureUnavailable }
    for (index, item) in cases.enumerated() {
        let (milliseconds, cancel) = item
        let path = directory.appendingPathComponent("result-\(index).json")
        let child = Process()
        child.executableURL = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        child.arguments = ["--fixture", path.path]
        try child.run()
        func read() -> [String: Any] {
            guard let data = try? Data(contentsOf: path) else { return [:] }
            return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
        }
        var pressed = false, cancelled = false
        var delivery: NativeInputDelivery?
        var lastPoint = CGPoint.zero
        var backgroundFocus: pid_t?
        var backgroundPointer: CGPoint?
        var probe: GlobalInputProbe?
        var downTime: Double?
        var failure: String?
        do {
            let deadline = ProcessInfo.processInfo.systemUptime + 5
            while (read()["ready"] as? Bool != true || NSWorkspace.shared.frontmostApplication?.processIdentifier != child.processIdentifier) && ProcessInfo.processInfo.systemUptime < deadline {
                CFRunLoopRunInMode(.defaultMode, 0.02, false)
            }
            // WindowServer can still be rendering the app-launch transition
            // after AppKit reports key status. This fixture settles its own
            // presentation before deriving input coordinates.
            CFRunLoopRunInMode(.defaultMode, 0.5, false)
            let state = read()
            guard state["ready"] as? Bool == true else { throw Stopped.fixtureUnavailable }
            let start = CGPoint(x: state["start_x"] as! Double, y: state["start_y"] as! Double)
            let end = CGPoint(x: state["end_x"] as! Double, y: state["end_y"] as! Double)
            delivery = NativeInputDelivery(pid: child.processIdentifier, window: CGWindowID(state["window_id"] as! Int), directed: directed)
            if directed, let original { original.activate(options: []) ; CFRunLoopRunInMode(.defaultMode,0.2,false) }
            probe = GlobalInputProbe()
            backgroundFocus = NSWorkspace.shared.frontmostApplication?.processIdentifier
            backgroundPointer = CGEvent(source:nil)?.location
            func check() throws {
                guard directed || NSWorkspace.shared.frontmostApplication?.processIdentifier == child.processIdentifier else { throw Stopped.targetChanged }
                if cancel, let downTime, ProcessInfo.processInfo.systemUptime - downTime >= 0.1 { throw Stopped.cancelled }
            }
            let sequence = try NativeDesktopInput.dragSequence(from: start, to: end, durationMilliseconds: milliseconds)
            func post(_ event: CGEvent) throws {
                try delivery!.post(event, check: check, didPost: {
                    lastPoint = event.location
                    if event.type == .leftMouseDown { pressed = true; downTime = ProcessInfo.processInfo.systemUptime }
                    if event.type == .leftMouseUp { pressed = false }
                })
            }
            if legacy { for event in sequence.events { try check(); try post(event) } }
            else { try sequence.deliver(check: check, post: post) }
        } catch Stopped.cancelled { cancelled = true }
        catch { failure = String(describing: error) }
        if pressed, let up = CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: lastPoint, mouseButton: .left) {
            try? delivery?.postAuxiliary(up)
        }
        let settle = ProcessInfo.processInfo.systemUptime + 0.2
        while ProcessInfo.processInfo.systemUptime < settle { CFRunLoopRunInMode(.defaultMode, 0.01, false) }
        let events = read()["events"] as? [[String: Any]] ?? []
        let down = events.filter { $0["type"] as? String == "down" }
        let up = events.filter { $0["type"] as? String == "up" }
        let held = down.count == 1 && up.count == 1 ? (up[0]["time_ms"] as! Double) - (down[0]["time_ms"] as! Double) : -1
        let eventHeld = down.count == 1 && up.count == 1 ? (up[0]["event_time_ms"] as! Double) - (down[0]["event_time_ms"] as! Double) : -1
        let timing = cancel ? held >= 75 && held < 1000 && cancelled : held >= Double(milliseconds) - 20 && held < Double(milliseconds) + 1500
        let endpoint = up.count == 1 && (cancel ? (up[0]["x"] as! Double) < 300 : abs((up[0]["x"] as! Double) - 300) < 1)
        let backgroundStable = !directed || ((backgroundFocus == NSWorkspace.shared.frontmostApplication?.processIdentifier || ((probe?.userEvents ?? 0) > 0 && NSWorkspace.shared.frontmostApplication?.processIdentifier != child.processIdentifier)) && (backgroundPointer == CGEvent(source:nil)?.location || (probe?.userEvents ?? 0) > 0) && probe?.tap != nil && probe?.ownEvents == 0)
        let passed = backgroundStable && failure == nil && timing && (cancel || eventHeld >= Double(milliseconds)) && endpoint && !CGEventSource.buttonState(.hidSystemState, button: .left)
        output(["duration_ms": milliseconds, "cancel": cancel, "legacy": legacy, "directed":directed, "background_stable":backgroundStable,"global_own_events":probe?.ownEvents ?? -1,"concurrent_user_events":probe?.userEvents ?? -1,"focus_before":backgroundFocus ?? -1,"focus_after":NSWorkspace.shared.frontmostApplication?.processIdentifier ?? -1,"pointer_before":String(describing:backgroundPointer),"pointer_after":String(describing:CGEvent(source:nil)?.location), "passed": passed, "held_ms": held, "event_held_ms": eventHeld, "error": failure ?? "", "events": events])
        if !passed { failures += 1 }
        let ownsFocus = NSWorkspace.shared.frontmostApplication?.processIdentifier == child.processIdentifier
        child.terminate(); child.waitUntilExit()
        if ownsFocus {
            if let original, !original.isTerminated { original.activate(options: []) }
            if let pointer, let move = CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: pointer, mouseButton: .left) {
                try? NativeInputDelivery.markAuxiliary(move); move.post(tap: .cghidEventTap)
            }
        }
    }
    try FileManager.default.removeItem(at: directory)
    exit(failures == 0 ? 0 : 1)
}
