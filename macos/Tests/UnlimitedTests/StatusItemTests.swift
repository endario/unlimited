import AppKit
import Testing
@testable import Unlimited

@MainActor
@Test func pointerMonitorReturnsTheMouseUpEventBeforeItsHoverUpdate() async throws {
    let event = try #require(NSEvent.mouseEvent(with: .leftMouseUp, location: .zero, modifierFlags: [],
                                               timestamp: 0, windowNumber: 0, context: nil,
                                               eventNumber: 0, clickCount: 1, pressure: 0))
    var updates = 0
    await withCheckedContinuation { continuation in
        let passed = AppDelegate.passThroughHoverEvent(event) {
            updates += 1
            continuation.resume()
        }
        #expect(passed === event)
        #expect(updates == 0)
    }
    #expect(updates == 1)
}

@MainActor
@Test func hoveringAtTheScreenEdgeUsesMouseHotspotBounds() {
    let rect = NSRect(x: 100, y: 1290, width: 200, height: 39)
    #expect(AppDelegate.hoverContains(NSPoint(x: 150, y: 1329), in: rect))
    #expect(AppDelegate.hoverContains(NSPoint(x: 150, y: 1328), in: rect))
    #expect(!AppDelegate.hoverContains(NSPoint(x: 150, y: 1289), in: rect))
    #expect(!AppDelegate.hoverContains(NSPoint(x: 301, y: 1329), in: rect))
}
