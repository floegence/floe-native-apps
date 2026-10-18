import ApplicationServices
import Foundation

/// Counts describe event-tap observation, never application-level success.
public struct NativeInputDeliveryProgress: Equatable {
    public let posted: Int
    public let observed: Int
    public init(posted: Int, observed: Int) { self.posted = posted; self.observed = observed }
}

public struct NativeInputDeliveryFailure: Error {
    public enum Reason: String { case unavailable, receiptTimeout = "receipt_timeout" }
    public let reason: Reason
    public let progress: NativeInputDeliveryProgress
}

// One outstanding event per delivery owner. A receipt from a completed or timed
// out event cannot satisfy a later event, including cleanup releases.
struct NativeInputReceipt {
    private(set) var marker: Int64 = 0
    private(set) var observed = false
    mutating func begin(marker: Int64) { self.marker = marker; observed = false }
    mutating func receive(_ marker: Int64) {
        if marker != 0 && marker == self.marker { observed = true }
    }
    mutating func finish() { marker = 0; observed = false }
}

/// Main-run-loop input delivery. The host owns target selection, authorization,
/// takeover, cancellation and release cleanup. No event is retried here.
public final class NativeInputDelivery {
    private static let markerLock = NSLock()
    private static var nextMarker = Int64.random(in: 1...(1 << 40))
    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private var receipt = NativeInputReceipt()
    private var posted = 0
    private var observed = 0

    public var progress: NativeInputDeliveryProgress { .init(posted: posted, observed: observed) }
    public var available: Bool { tap.map { CGEvent.tapIsEnabled(tap: $0) } ?? false }

    /// Nil observes session delivery; a PID observes that process's tail tap.
    public init(pid: pid_t? = nil) {
        let types: [CGEventType] = [.keyDown, .keyUp, .flagsChanged, .leftMouseDown, .leftMouseUp,
            .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp, .mouseMoved,
            .leftMouseDragged, .rightMouseDragged, .otherMouseDragged, .scrollWheel]
        let mask = types.reduce(CGEventMask(0)) { $0 | (1 << $1.rawValue) }
        let callback: CGEventTapCallBack = { _, _, event, pointer in
            if let pointer {
                let owner = Unmanaged<NativeInputDelivery>.fromOpaque(pointer).takeUnretainedValue()
                owner.receipt.receive(event.getIntegerValueField(.eventSourceUserData))
            }
            return Unmanaged.passUnretained(event)
        }
        let pointer = Unmanaged.passUnretained(self).toOpaque()
        if let pid {
            tap = CGEvent.tapCreateForPid(pid: pid, place: .tailAppendEventTap, options: .listenOnly,
                eventsOfInterest: mask, callback: callback, userInfo: pointer)
        } else {
            tap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
                eventsOfInterest: mask, callback: callback, userInfo: pointer)
        }
        if let tap {
            source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
            if let source { CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes) }
            CGEvent.tapEnable(tap: tap, enable: true)
        }
    }

    deinit {
        if let tap { CGEvent.tapEnable(tap: tap, enable: false); CFMachPortInvalidate(tap) }
        if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes) }
    }

    /// All waits share this event's single monotonic deadline. check may throw
    /// on cancellation/takeover; didPost lets the host retain necessary releases.
    public func post(_ event: CGEvent, timeout: TimeInterval = 1,
                     check: () throws -> Void = {}, didPost: () -> Void = {}) throws {
        try check()
        guard available else { throw NativeInputDeliveryFailure(reason: .unavailable, progress: progress) }
        let marker = Self.allocateMarker()
        receipt.begin(marker: marker)
        defer { receipt.finish() }
        try Self.mark(event, marker: marker)
        let deadline = ProcessInfo.processInfo.systemUptime + max(0, timeout)
        event.post(tap: .cghidEventTap)
        posted += 1
        didPost()
        while !receipt.observed {
            try check()
            let remaining = deadline - ProcessInfo.processInfo.systemUptime
            guard remaining > 0, available else {
                throw NativeInputDeliveryFailure(reason: available ? .receiptTimeout : .unavailable, progress: progress)
            }
            CFRunLoopRunInMode(.defaultMode, remaining, true)
        }
        observed += 1
        try check()
    }

    /// A unique marker for host-owned cleanup; it can never confirm an input.
    public static func markAuxiliary(_ event: CGEvent) throws {
        try mark(event, marker: allocateMarker())
    }

    private static func mark(_ event: CGEvent, marker: Int64) throws {
        guard let source = CGEventSource(stateID: .privateState) else {
            throw NativeInputDeliveryFailure(reason: .unavailable, progress: .init(posted: 0, observed: 0))
        }
        // Replacing the source also replaces cached userData on copied events.
        source.userData = marker
        event.setSource(source)
    }

    private static func allocateMarker() -> Int64 {
        markerLock.lock(); defer { markerLock.unlock() }
        // Exhaust rather than reuse a token, including across delivery owners.
        precondition(nextMarker < (1 << 52))
        nextMarker += 1
        return nextMarker
    }
}
