import AppKit
import FloeNativeDesktop

// A task-owned native fixture. Probe performs no capture or input. Session mode
// uses the same public engine as a consumer and never changes OS permissions.
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let writer = NSLock()
final class InteractionFixture: NSObject, NSTextViewDelegate {
    let window = NSWindow(contentRect: NSRect(x: 120, y: 160, width: 640, height: 400),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
    let text = NSTextView(frame: NSRect(x: 20, y: 70, width: 600, height: 300))
    let button = NSButton(frame: NSRect(x: 20, y: 20, width: 260, height: 32))
    let marker = NSView(frame: NSRect(x: 320, y: 20, width: 260, height: 32))
    var clicks = 0
    var changes = 0
    var clipboardSnapshot: [[NSPasteboard.PasteboardType: Data]]?
    var clipboardWriteCount: Int?
    var motion: Timer?
    var displayLink: AnyObject?
    var advanceMotion: (() -> Void)?
    var office: NSScrollView?
    override init() {
        super.init()
        window.title = "Floe task-owned desktop interaction fixture"
        window.isReleasedWhenClosed = false
        text.isRichText = false; text.font = .systemFont(ofSize: 20); text.delegate = self
        window.contentView?.addSubview(text)
        button.title = "Task pointer target"; button.target = self; button.action = #selector(clicked)
        window.contentView?.addSubview(button)
        marker.wantsLayer = true; marker.layer?.backgroundColor = NSColor.blue.cgColor
        window.contentView?.addSubview(marker)
        window.makeKeyAndOrderFront(nil); window.makeFirstResponder(text)
        application.activate(ignoringOtherApps: true)
    }
    @objc func clicked() {
        clicks += 1
        changed()
    }
    func textDidChange(_ notification: Notification) { changed() }
    private func changed() {
        changes += 1
        CATransaction.begin(); CATransaction.setDisableActions(true)
        marker.layer?.backgroundColor = (changes % 2 == 0 ? NSColor.blue : NSColor.red).cgColor
        CATransaction.commit()
        output(["type": "fixture_input", "changes": changes, "timestamp": ProcessInfo.processInfo.systemUptime])
    }
    func clipboard(_ request: [String: Any]) {
        let pasteboard = NSPasteboard.general
        if let value = request["text"] as? String {
            if clipboardSnapshot == nil {
                clipboardSnapshot = (pasteboard.pasteboardItems ?? []).map { item in
                    Dictionary(uniqueKeysWithValues: item.types.compactMap { type in item.data(forType: type).map { (type, $0) } })
                }
            }
            pasteboard.clearContents(); pasteboard.setString(value, forType: .string)
            clipboardWriteCount = pasteboard.changeCount
        }
        // Only task-owned contents are compared. Original user data is not emitted.
        if let expected = request["expected"] as? String {
            let matches = pasteboard.string(forType: .string) == expected
            if matches { clipboardWriteCount = pasteboard.changeCount }
            output(["type": "fixture_clipboard", "matches": matches])
        }
    }
    func restoreClipboard() {
        let pasteboard = NSPasteboard.general
        guard let snapshot = clipboardSnapshot, pasteboard.changeCount == clipboardWriteCount else { return }
        pasteboard.clearContents()
        let items = snapshot.map { values in
            let item = NSPasteboardItem()
            for (type, data) in values { item.setData(data, forType: type) }
            return item
        }
        if !items.isEmpty { pasteboard.writeObjects(items) }
        clipboardSnapshot = nil
    }
    func coverDesktop() {
        guard let screen = window.screen ?? NSScreen.main else { return }
        // A static qualification surface excludes unrelated windows and menu
        // clocks without changing capture policy or the host display mode.
        application.presentationOptions = [.hideDock, .hideMenuBar]
        window.styleMask = [.borderless]
        window.setFrame(screen.frame, display: true)
        window.makeKeyAndOrderFront(nil)
        application.activate(ignoringOtherApps: true)
    }
    func animate(_ scene: String) {
        motion?.invalidate(); motion = nil
        if #available(macOS 14.0, *) { (displayLink as? CADisplayLink)?.invalidate() }
        displayLink = nil; advanceMotion = nil
        if scene == "freeze" { output(["type": "fixture_motion", "scene": scene, "timestamp": ProcessInfo.processInfo.systemUptime]); return }
        guard ["scroll", "window"].contains(scene), let screen = window.screen ?? NSScreen.main else { return }
        let available = window.styleMask.contains(.titled) ? screen.visibleFrame : screen.frame
        let frame = scene == "scroll" ? available : available.insetBy(dx: 120, dy: 120)
        window.setFrame(frame, display: true)
        let scroll = NSScrollView(frame: window.contentView!.bounds)
        scroll.autoresizingMask = [.width, .height]
        scroll.hasVerticalScroller = true; scroll.scrollerStyle = .legacy
        let document = NSTextView(frame: NSRect(x: 0, y: 0, width: scroll.contentSize.width, height: 100_000))
        document.isRichText = false; document.isEditable = false; document.isSelectable = false
        document.font = .monospacedSystemFont(ofSize: 18, weight: .regular)
        document.string = (0..<2500).map { "Office fixture row \($0): document text, numbers 0123456789 and punctuation.\n" }.joined()
        scroll.documentView = document
        office?.removeFromSuperview(); office = scroll
        window.contentView?.addSubview(scroll)
        let started = ProcessInfo.processInfo.systemUptime
        var ticks = 0
        advanceMotion = { [weak self] in
            guard let self else { return }
            let elapsed = ProcessInfo.processInfo.systemUptime - started
            if scene == "scroll" {
                let phase = elapsed.truncatingRemainder(dividingBy: 150)
                scroll.contentView.scroll(to: NSPoint(x: 0, y: min(phase, 150 - phase) * 240))
                scroll.reflectScrolledClipView(scroll.contentView)
            } else {
                self.window.setFrameOrigin(NSPoint(x: frame.minX + sin(elapsed * 1.5) * 100,
                                                  y: frame.minY + cos(elapsed * 1.5) * 100))
            }
            ticks += 1
            if ticks % 60 == 0 { output(["type": "fixture_motion", "scene": scene, "ticks": ticks, "elapsed": elapsed]) }
        }
        if #available(macOS 14.0, *) {
            let link = window.displayLink(target: self, selector: #selector(advanceDisplay(_:)))
            link.preferredFrameRateRange = CAFrameRateRange(minimum: 60, maximum: 60, preferred: 60)
            displayLink = link; link.add(to: .main, forMode: .common)
        } else {
            let timer = Timer(timeInterval: 1.0 / 60, repeats: true) { [weak self] _ in self?.advanceMotion?() }
            motion = timer; RunLoop.main.add(timer, forMode: .common)
        }
        output(["type": "fixture_motion", "scene": scene])
    }
    @objc func advanceDisplay(_ sender: Any) { advanceMotion?() }
    func status() -> [String: Any] {
        let rect = window.convertToScreen(button.convert(button.bounds, to: nil))
        let markerRect = window.convertToScreen(marker.convert(marker.bounds, to: nil))
        let desktop = CGDisplayBounds(CGMainDisplayID())
        return ["type": "fixture", "text": text.string, "clicks": clicks,
                "active": window.isKeyWindow && NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier,
                "key_window": window.isKeyWindow, "application_active": application.isActive,
                "frontmost": NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier,
                "marker_x": (markerRect.midX - desktop.minX) / max(1, desktop.width - 1),
                "marker_y": (desktop.height - markerRect.midY - desktop.minY) / max(1, desktop.height - 1),
                "x": (rect.midX - desktop.minX) / max(1, desktop.width - 1),
                "y": (desktop.height - rect.midY - desktop.minY) / max(1, desktop.height - 1)]
    }
}
func output(_ message: [String: Any]) {
    guard let bytes = try? JSONSerialization.data(withJSONObject: message, options: [.sortedKeys]) else { return }
    writer.lock(); defer { writer.unlock() }
    FileHandle.standardOutput.write(bytes)
    FileHandle.standardOutput.write(Data([10]))
}
if CommandLine.arguments.dropFirst().first == "probe" {
    output(NativeDesktopSession.capabilities())
} else if CommandLine.arguments.dropFirst().first == "profile-checks" {
    func measure(_ name: String, _ body: () -> Void) {
        let started = ProcessInfo.processInfo.systemUptime
        for _ in 0..<50 { body() }
        output(["operation": name, "mean_ms": (ProcessInfo.processInfo.systemUptime - started) * 20])
    }
    measure("readiness") { _ = NativeDesktopSession.readiness }
    measure("session_identity") { _ = CGSessionCopyCurrentDictionary() }
    measure("screen_permission") { _ = CGPreflightScreenCaptureAccess() }
    measure("displays") { _ = NativeDesktopSession.displays() }
    measure("accessibility") { _ = AXIsProcessTrusted() }
    measure("fixture_foreground") { _ = NSWorkspace.shared.frontmostApplication?.processIdentifier }
} else if ["session", "fixture"].contains(CommandLine.arguments.dropFirst().first ?? "") {
    let fixture: InteractionFixture?
    if CommandLine.arguments.dropFirst().first == "fixture" {
        application.setActivationPolicy(.regular)
        application.finishLaunching()
        fixture = InteractionFixture()
        output(fixture!.status())
    } else { fixture = nil }
    let session = NativeDesktopSession(mayControl: {
        fixture == nil || (fixture!.window.isKeyWindow && NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier)
    }, output: output)
    DispatchQueue.global(qos: .userInitiated).async {
        while let line = readLine(strippingNewline: true) {
            guard line.utf8.count <= 8 << 20, let bytes = line.data(using: .utf8),
                  let request = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any] else { continue }
            DispatchQueue.main.async {
                if let fixture, request["method"] as? String == "fixture_status" { output(fixture.status()) }
                else if let fixture, request["method"] as? String == "fixture_cover" { fixture.coverDesktop() }
                else if let fixture, request["method"] as? String == "fixture_clipboard" { fixture.clipboard(request) }
                else if let fixture, request["method"] as? String == "fixture_animate", let scene = request["scene"] as? String { fixture.animate(scene) }
                else if let fixture, request["method"] as? String == "fixture_focus" {
                    fixture.window.makeKeyAndOrderFront(nil); fixture.window.makeFirstResponder(fixture.text)
                    application.activate(ignoringOtherApps: true)
                    output(fixture.status())
                } else {
                    let started = ProcessInfo.processInfo.systemUptime
                    session.handle(request)
                    if fixture != nil, request["method"] as? String == "input", ProcessInfo.processInfo.environment["FLOE_QUALIFY_INPUT_TIMING"] == "1" {
                        output(["type": "fixture_dispatch", "input_id": request["id"] ?? 0,
                                "kind": (request["input"] as? [String: Any])?["kind"] ?? "",
                                "started": started, "finished": ProcessInfo.processInfo.systemUptime])
                    }
                }
            }
        }
        DispatchQueue.main.async { session.close { fixture?.restoreClipboard(); application.terminate(nil) } }
    }
    application.run()
} else {
    FileHandle.standardError.write(Data("Usage: NativeDesktopQualification probe|profile-checks|session|fixture\n".utf8))
    exit(2)
}
