import ApplicationServices
import Darwin
import Foundation

// Optional WindowServer window-local protocol (also documented in DeepSeek
// Harness PIDEventPoster/WindowPointerInput). No foreground fallback or retry.
struct NativeWindowInputRoute {
    private typealias WindowLocation = @convention(c) (CGEvent, CGFloat, CGFloat) -> Void
    private typealias Send = @convention(c) (pid_t, CGEvent) -> Void
    private static let library = dlopen("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY | RTLD_LOCAL)
    private static let location: WindowLocation? = {
        guard let library, let symbol = dlsym(library, "CGEventSetWindowLocation") else { return nil }
        return unsafeBitCast(symbol, to: WindowLocation.self)
    }()
    private static let send: Send? = {
        guard let library, let symbol = dlsym(library, "SLEventPostToPid") else { return nil }
        return unsafeBitCast(symbol, to: Send.self)
    }()
    let pid: pid_t
    let window: CGWindowID
    var available: Bool { Self.location != nil && Self.send != nil && bounds != nil }
    private var bounds: CGRect? {
        guard let items = CGWindowListCopyWindowInfo([.optionIncludingWindow], window) as? [[String: Any]],
              let item = items.first,
              (item[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
              let value = item[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: value as CFDictionary) else { return nil }
        return bounds
    }
    func post(_ event: CGEvent) throws {
        guard let rect = bounds, let location = Self.location, let send = Self.send else {
            throw NativeDesktopError(code: "TARGET_NOT_READY", message: "Window-directed input is unavailable for this target.")
        }
        let pointerTypes: [CGEventType] = [.mouseMoved, .leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp,
            .otherMouseDown, .otherMouseUp, .leftMouseDragged, .rightMouseDragged, .otherMouseDragged, .scrollWheel]
        if pointerTypes.contains(event.type) {
            let point = event.location
            location(event, point.x - rect.minX, point.y - rect.minY)
            event.setIntegerValueField(.mouseEventSubtype, value: 3)
        }
        event.setIntegerValueField(.eventTargetUnixProcessID, value: Int64(pid))
        for field in [51, 91, 92] {
            if let key = CGEventField(rawValue: UInt32(field)) { event.setIntegerValueField(key, value: Int64(window)) }
        }
        event.timestamp = DispatchTime.now().uptimeNanoseconds
        send(pid, event)
    }
}
