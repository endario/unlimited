import AppKit
import SwiftUI
import Testing
@testable import Unlimited
@testable import UnlimitedKit

@MainActor
private func usageImage(_ tile: Tile) throws -> NSBitmapImageRep {
    _ = NSApplication.shared
    let defaults = try #require(UserDefaults(suiteName: "AccountUsageTests-\(UUID())"))
    let host = NSHostingView(rootView: AccountRow(model: StripModel(defaults: defaults), tile: tile)
        .padding(10).frame(width: 560, height: 46).environment(\.colorScheme, .dark))
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 46),
                          styleMask: [.borderless], backing: .buffered, defer: false)
    window.contentView = host
    host.layoutSubtreeIfNeeded()
    let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
    host.cacheDisplay(in: host.bounds, to: image)
    return image
}

@MainActor
@Test func usageFiguresOnlyAppendPercentToKnownValues() {
    for (value, text) in [(Tile.Value.percent(0), "0%"), (.percent(100), "100%"),
                          (.unread, "?"), (.stale, "?"), (.unknown, "?"),
                          (.noWindow, "—"), (.waiting, "…")] {
        #expect(UsageFigure(value: value, health: .normal, percentSign: true).text == text)
        #expect(UsageFigure(value: value, health: .normal).text == value.text)
    }
}

@MainActor
@Test func settingsUsageUsesPrimaryHealthRatherThanAlternateWarning() throws {
    let tile = Tile(id: "openai/test", label: "CDX", value: .percent(27), dimmed: false,
                    health: .underUsed,
                    alternate: .init(role: "session", value: .percent(99), health: .red))
    let image = try usageImage(tile)
    var greenPixels = 0
    for y in 0..<image.pixelsHigh {
        for x in 0..<image.pixelsWide {
            let c = try #require(image.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
            if c.greenComponent > 0.7 && c.greenComponent > c.redComponent * 1.3 &&
                c.greenComponent > c.blueComponent * 1.2 { greenPixels += 1 }
        }
    }
    #expect(greenPixels > 10)
}
