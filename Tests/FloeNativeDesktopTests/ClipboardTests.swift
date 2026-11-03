import AppKit
import XCTest
@testable import FloeNativeDesktop
final class ClipboardTests: XCTestCase {
    func testFormatsAndConcurrentUserCopy() throws {
        let board=NSPasteboard.withUniqueName(); defer {board.releaseGlobally()}
        try NativeClipboard.write([["public.utf8-plain-text":Data("original".utf8),"public.html":Data("<b>original</b>".utf8)]],to:board)
        let original=try NativeClipboard.read(board)
        let temporary=try NativeClipboard.write([["public.utf8-plain-text":Data("temporary".utf8)]],to:board)
        XCTAssertTrue(try NativeClipboard.restore(original,ifUnchanged:temporary,to:board))
        XCTAssertEqual(try NativeClipboard.read(board).items,original.items)
        let version=try NativeClipboard.write([["public.utf8-plain-text":Data("temporary".utf8)]],to:board)
        try NativeClipboard.write([["public.utf8-plain-text":Data("user copy".utf8)]],to:board)
        XCTAssertFalse(try NativeClipboard.restore(original,ifUnchanged:version,to:board))
        XCTAssertEqual(board.string(forType:.string),"user copy")
        XCTAssertThrowsError(try NativeClipboard.read(board,maximumBytes:1))
    }
}
