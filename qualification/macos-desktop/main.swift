import AppKit
import Carbon
import FloeNativeDesktop
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

// A task-owned native fixture. Probe performs no capture or input. Session mode
// uses the same public engine as a consumer and never changes OS permissions.
let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let writer = NSLock()
final class FixtureWindow: NSWindow {
    // The covered qualification surface is borderless but must still accept
    // focus. Ordinary NSWindow drops key-window eligibility without a title bar.
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}
final class InteractionFixture: NSObject, NSTextViewDelegate {
    let window = FixtureWindow(contentRect: NSRect(x: 120, y: 160, width: 640, height: 400),
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
    var backdrop: NSWindow?
    var focusObservers: [NSObjectProtocol] = []
    var keyObserver: Any?
    var latencyMode = false
    override init() {
        super.init()
        // A normal AppKit Edit menu owns Command-key equivalents. Without it,
        // this fixture cannot qualify the viewer's real clipboard shortcut.
        let menu = NSMenu()
        let applicationMenu = NSMenuItem(title: "Fixture", action: nil, keyEquivalent: "")
        applicationMenu.submenu = NSMenu(title: "Fixture")
        menu.addItem(applicationMenu)
        let edit = NSMenu(title: "Edit")
        for (title, action, key) in [("Cut", #selector(NSText.cut(_:)), "x"),
                                     ("Copy", #selector(NSText.copy(_:)), "c"),
                                     ("Paste", #selector(NSText.paste(_:)), "v"),
                                     ("Select All", #selector(NSText.selectAll(_:)), "a")] {
            edit.addItem(withTitle: title, action: action, keyEquivalent: key)
        }
        let editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        editItem.submenu = edit; menu.addItem(editItem); application.mainMenu = menu
        window.title = "Floe task-owned desktop interaction fixture"
        window.isReleasedWhenClosed = false
        text.isRichText = false; text.font = .systemFont(ofSize: 20); text.delegate = self
        window.contentView?.addSubview(text)
        button.title = "Task pointer target"; button.target = self; button.action = #selector(clicked)
        window.contentView?.addSubview(button)
        marker.wantsLayer = true; marker.layer?.backgroundColor = NSColor.blue.cgColor
        window.contentView?.addSubview(marker)
        for name in [NSWindow.didBecomeKeyNotification, NSWindow.didResignKeyNotification,
                     NSApplication.didBecomeActiveNotification, NSApplication.didResignActiveNotification] {
            focusObservers.append(NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                if name == NSApplication.didBecomeActiveNotification, let self {
                    self.window.makeKeyAndOrderFront(nil); self.window.makeFirstResponder(self.text)
                }
                DispatchQueue.main.async { if let self { output(self.status()) } }
            })
        }
        keyObserver = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp, .flagsChanged]) { [weak self] event in
            if self?.latencyMode == true, event.type == .keyDown, event.keyCode == 12 { self?.changed() }
            if ProcessInfo.processInfo.environment["FLOE_QUALIFY_KEY_TIMING"] == "1" {
                output(["type": "fixture_key", "key_code": event.keyCode, "flags": event.modifierFlags.rawValue,
                        "unicode_length": event.type == .flagsChanged ? 0 : (event.characters?.utf16.count ?? 0),
                        "wall_time": Date().timeIntervalSince1970])
            }
            // This visible response fixture handles Q itself, like an app
            // shortcut. An IME candidate is not a committed text change and
            // must not determine whether the benchmark notices native input.
            if self?.latencyMode == true, event.keyCode == 12 { return nil }
            return event
        }
        window.makeKeyAndOrderFront(nil); window.makeFirstResponder(text)
        application.activate(ignoringOtherApps: true)
    }
    @objc func clicked() {
        clicks += 1
        changed()
    }
    func textDidChange(_ notification: Notification) { if !latencyMode { changed() } }
    private func changed() {
        changes += 1
        CATransaction.begin(); CATransaction.setDisableActions(true)
        marker.layer?.backgroundColor = (changes % 2 == 0 ? NSColor.blue : NSColor.red).cgColor
        CATransaction.commit()
        output(["type": "fixture_input", "changes": changes, "timestamp": ProcessInfo.processInfo.systemUptime,
                "wall_time": Date().timeIntervalSince1970])
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
        if backdrop == nil {
            let background = NSWindow(contentRect: screen.frame, styleMask: [.borderless], backing: .buffered, defer: false)
            background.isReleasedWhenClosed = false
            background.backgroundColor = .windowBackgroundColor
            background.ignoresMouseEvents = true
            background.order(.below, relativeTo: window.windowNumber)
            backdrop = background
        }
        window.styleMask = [.borderless]
        window.setFrame(screen.frame, display: true)
        window.makeKeyAndOrderFront(nil)
        application.activate(ignoringOtherApps: true)
    }
    func prepareOffice() {
        guard let content = window.contentView, content.bounds.height > 500 else { return }
        let scroll = NSScrollView(frame: NSRect(x: 20, y: 410, width: content.bounds.width - 40,
                                               height: content.bounds.height - 430))
        scroll.hasVerticalScroller = true; scroll.scrollerStyle = .legacy
        let document = NSTextView(frame: NSRect(x: 0, y: 0, width: scroll.contentSize.width, height: 100_000))
        document.isRichText = false; document.isEditable = false
        document.font = .monospacedSystemFont(ofSize: 18, weight: .regular)
        document.string = (0..<2500).map { "Task office row \($0): scrolling and selection.\n" }.joined()
        scroll.documentView = document
        office?.removeFromSuperview(); office = scroll
        content.addSubview(scroll)
        output(status())
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
    func seedText(_ request: [String: Any]) {
        guard status()["active"] as? Bool == true,
              let value = request["text"] as? String, value.utf8.count <= 16000 else { return }
        text.string = value
        output(status())
    }
    func reference() {
        guard #available(macOS 14.0, *), let screen = window.screen,
              window.frame == screen.frame, status()["active"] as? Bool == true else {
            output(["type": "fixture_reference", "error": "FIXTURE_NOT_COVERED"]); return
        }
        let displayID = (screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as! NSNumber).uint32Value
        let scale = CGFloat(CGDisplayCopyDisplayMode(displayID)!.pixelWidth) / screen.frame.width
        let textRect = window.convertToScreen(text.convert(text.bounds, to: nil))
        let rectangle = CGRect(x: (textRect.minX - screen.frame.minX) * scale,
                               y: (screen.frame.maxY - textRect.maxY) * scale,
                               width: 800, height: 120).integral
        SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { content, error in
            guard error == nil, let display = content?.displays.first(where: { $0.displayID == displayID }) else {
                output(["type": "fixture_reference", "error": "REFERENCE_DISPLAY_UNAVAILABLE"]); return
            }
            let configuration = SCStreamConfiguration()
            configuration.width = Int(screen.frame.width * scale)
            configuration.height = Int(screen.frame.height * scale)
            configuration.showsCursor = false
            configuration.pixelFormat = kCVPixelFormatType_32BGRA
            configuration.colorSpaceName = CGColorSpace.sRGB
            SCScreenshotManager.captureImage(contentFilter: SCContentFilter(display: display, excludingWindows: []), configuration: configuration) { image, error in
                guard error == nil, let image = image?.cropping(to: rectangle) else {
                    output(["type": "fixture_reference", "error": "REFERENCE_CAPTURE_FAILED"]); return
                }
                DispatchQueue.main.async {
                    guard self.status()["active"] as? Bool == true else {
                        output(["type": "fixture_reference", "error": "FIXTURE_NOT_FOCUSED"]); return
                    }
                    let data = NSMutableData()
                    guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else { return }
                    CGImageDestinationAddImage(destination, image, nil)
                    guard CGImageDestinationFinalize(destination) else { return }
                    output(["type": "reference", "rectangle": [Int(rectangle.minX), Int(rectangle.minY), 800, 120],
                            "width": configuration.width, "height": configuration.height,
                            "source": "independent ScreenCaptureKit screenshot", "png": (data as Data).base64EncodedString()])
                }
            }
        }
    }
    func status() -> [String: Any] {
        let rect = window.convertToScreen(button.convert(button.bounds, to: nil))
        let markerRect = window.convertToScreen(marker.convert(marker.bounds, to: nil))
        let textRect = window.convertToScreen(text.convert(text.bounds, to: nil))
        let desktop = CGDisplayBounds(CGMainDisplayID())
        let source = TISCopyCurrentKeyboardInputSource().takeRetainedValue()
        let sourceID = TISGetInputSourceProperty(source, kTISPropertyInputSourceID).map { Unmanaged<CFString>.fromOpaque($0).takeUnretainedValue() as String } ?? ""
        return ["type": "fixture", "text": text.string, "clicks": clicks,
                "width": desktop.width, "height": desktop.height,
                "marked_text": text.hasMarkedText(), "input_source": sourceID,
                "selection": [text.selectedRange().location, NSMaxRange(text.selectedRange())],
                "entry_x": (textRect.minX + text.textContainerInset.width + 4 - desktop.minX) / max(1, desktop.width - 1),
                "entry_y": (desktop.height - textRect.maxY + text.textContainerInset.height + 10 - desktop.minY) / max(1, desktop.height - 1),
                "scroll_y": office?.contentView.bounds.minY ?? 0,
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
} else if CommandLine.arguments.dropFirst().first == "window-streams" {
    application.setActivationPolicy(.regular)
    application.finishLaunching()
    let fixture = InteractionFixture()
    var streams: [NativeCaptureStream] = []
    var started = Set<Int>(), painted = Set<Int>()
    var retiring = false
    var receipts: [[String: Any]] = []
    func finish(_ error: String? = nil) {
        guard !retiring else { return }; retiring = true
        var remaining = streams.count
        func done() {
            output(["type": "window_streams", "passed": error == nil, "error": error ?? "", "streams": receipts])
            if error != nil { exit(1) }
            application.terminate(nil)
        }
        if remaining == 0 { done(); return }
        for stream in streams { stream.stop { remaining -= 1; if remaining == 0 { done() } } }
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + 15) { finish("STREAM_TIMEOUT") }
    // Allow the task window's initial presentation to reach WindowServer before
    // ScreenCaptureKit snapshots its list of currently shareable windows.
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
        SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { content, error in
            DispatchQueue.main.async {
                guard !retiring else { return }
                guard error == nil, let content,
                      let window = content.windows.first(where: {
                          $0.owningApplication?.processID == ProcessInfo.processInfo.processIdentifier &&
                          $0.windowID == CGWindowID(fixture.window.windowNumber)
                      }), let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() }) else {
                    output(["type": "window_streams_readiness", "native_error": (error as NSError?)?.code ?? 0,
                            "window_id": fixture.window.windowNumber,
                            "owned_windows": content?.windows.filter { $0.owningApplication?.processID == ProcessInfo.processInfo.processIdentifier }.map { $0.windowID } ?? [],
                            "fixture_active": fixture.status()["active"] ?? false])
                    finish("FIXTURE_WINDOW_UNAVAILABLE"); return
                }
                for index in 0..<3 {
                    let settings = try! NativeCaptureSettings(request: ["video": index != 0, "require_video": index != 0,
                        "frame_capacity": index == 2 ? 4 : 1, "max_dimension": 1920, "frame_rate": 60])
                    let stream = NativeCaptureStream(generation: index + 1, settings: settings, output: { message in
                        DispatchQueue.main.async {
                            guard !retiring, !painted.contains(index) else { return }
                            guard started.contains(index), message["generation"] as? Int == index + 1,
                                  message["codec"] as? String == (index == 0 ? "jpeg" : "h264"),
                                  let frame = message["frame_id"] as? Int,
                                  let data = message["data"] as? String, !data.isEmpty else { finish("STREAM_CONTRACT_INVALID"); return }
                            streams[index].acknowledge(frame); painted.insert(index)
                            receipts.append(["target": index == 2 ? "display" : "owned_window", "generation": index + 1,
                                "codec": message["codec"]!, "width": message["width"]!, "height": message["height"]!])
                            if painted.count == 3 { finish() }
                        }
                    }, failed: { _ in finish("CAPTURE_FAILED") })
                    streams.append(stream)
                    if index == 2 { stream.start(display) { started.insert(index) } }
                    else { stream.start(window) { started.insert(index) } }
                }
            }
        }
    }
    application.run()
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
        if let raw = ProcessInfo.processInfo.environment["FLOE_QUALIFY_TARGET_PID"], let target = Int32(raw) {
            return NSWorkspace.shared.frontmostApplication?.processIdentifier == target
        }
        return fixture == nil || (fixture!.window.isKeyWindow && NSWorkspace.shared.frontmostApplication?.processIdentifier == ProcessInfo.processInfo.processIdentifier)
    }, output: output)
    DispatchQueue.global(qos: .userInitiated).async {
        while let line = readLine(strippingNewline: true) {
            guard line.utf8.count <= 8 << 20, let bytes = line.data(using: .utf8),
                  let request = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any] else { continue }
            DispatchQueue.main.async {
                if let fixture, request["method"] as? String == "fixture_status" { output(fixture.status()) }
                else if let fixture, request["method"] as? String == "fixture_cover" { fixture.coverDesktop() }
                else if let fixture, request["method"] as? String == "fixture_office" { fixture.prepareOffice() }
                else if let fixture, request["method"] as? String == "fixture_reference_focus" { fixture.window.makeFirstResponder(fixture.button) }
                else if let fixture, request["method"] as? String == "fixture_reference" { fixture.reference() }
                else if let fixture, request["method"] as? String == "fixture_latency" { fixture.latencyMode = request["enabled"] as? Bool == true }
                else if let fixture, request["method"] as? String == "fixture_seed" { fixture.seedText(request) }
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
    FileHandle.standardError.write(Data("Usage: NativeDesktopQualification probe|window-streams|profile-checks|session|fixture\n".utf8))
    exit(2)
}
