import ApplicationServices
import Foundation

/// An ordered input sequence. The host supplies the checked, acknowledged
/// posting operation and owns cleanup if delivery stops. Never replays input.
public struct NativeInputSequence {
    public let events: [CGEvent]
    private let offsets: [TimeInterval]
    private let anchorIndex: Int?

    /// Ordinary input has no intentional delay.
    public init(events: [CGEvent]) {
        self.events = events
        offsets = Array(repeating: 0, count: events.count)
        anchorIndex = nil
    }

    init(events: [CGEvent], offsets: [TimeInterval], anchorIndex: Int) {
        precondition(events.count == offsets.count && events.indices.contains(anchorIndex))
        self.events = events
        self.offsets = offsets
        self.anchorIndex = anchorIndex
    }

    /// Runs on the main run loop. Drag offsets share one monotonic origin after
    /// acknowledged mouse-down, so receipt latency never accumulates as sleeps.
    /// Wait slices are capped at 10 ms, with check before each slice and post.
    /// A thrown check/post stops immediately; the host retains prior effects.
    public func deliver(check: () throws -> Void = {}, post: (CGEvent) throws -> Void) throws {
        try deliver(now: { ProcessInfo.processInfo.systemUptime }, wait: {
            CFRunLoopRunInMode(.defaultMode, $0, true)
        }, check: check, post: post)
    }

    func deliver(now: () -> TimeInterval, wait: (TimeInterval) -> Void,
                 check: () throws -> Void, post: (CGEvent) throws -> Void) throws {
        var origin: TimeInterval?
        for (index, event) in events.enumerated() {
            if let origin {
                let deadline = origin + offsets[index]
                while true {
                    try check()
                    let remaining = deadline - now()
                    if remaining <= 0 { break }
                    wait(min(remaining, 0.01))
                }
            }
            try check()
            try post(event)
            if index == anchorIndex { origin = now() }
        }
    }
}
