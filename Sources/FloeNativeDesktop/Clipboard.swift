import AppKit

/// Explicit access to the shared pasteboard. Hosts own authorization and lifetime.
/// Version checking prevents restoring over a user's concurrent copy.
public enum NativeClipboard {
    public struct Snapshot {
        public let changeCount: Int
        public let items: [[String: Data]]
    }
    public static func read(_ pasteboard: NSPasteboard = .general, maximumBytes: Int = 1 << 20) throws -> Snapshot {
        let version = pasteboard.changeCount
        var total = 0
        let items = try (pasteboard.pasteboardItems ?? []).map { item -> [String: Data] in
            var values: [String: Data] = [:]
            for type in item.types {
                guard let data = item.data(forType: type) else { throw NativeDesktopInput.unavailable() }
                total += data.count
                guard total <= maximumBytes else { throw NativeDesktopInput.invalid("Clipboard exceeds the requested byte budget.") }
                values[type.rawValue] = data
            }
            return values
        }
        guard pasteboard.changeCount == version else { throw NativeDesktopInput.unavailable() }
        return Snapshot(changeCount: version, items: items)
    }
    @discardableResult public static func write(_ items: [[String: Data]], to pasteboard: NSPasteboard = .general, maximumBytes: Int = 1 << 20) throws -> Int {
        guard items.count <= 64, items.allSatisfy({ $0.count <= 64 }), items.reduce(0, { $0 + $1.values.reduce(0, {$0 + $1.count}) }) <= maximumBytes else { throw NativeDesktopInput.invalid("Clipboard exceeds the byte budget.") }
        let output = items.map { values -> NSPasteboardItem in
            let item = NSPasteboardItem()
            for (name, data) in values { item.setData(data, forType: NSPasteboard.PasteboardType(name)) }
            return item
        }
        pasteboard.clearContents()
        guard output.isEmpty || pasteboard.writeObjects(output) else { throw NativeDesktopInput.unavailable() }
        return pasteboard.changeCount
    }
    public static func restore(_ snapshot: Snapshot, ifUnchanged version: Int, to pasteboard: NSPasteboard = .general) throws -> Bool {
        guard pasteboard.changeCount == version else { return false }
        try write(snapshot.items, to: pasteboard)
        return true
    }
}
