import AppKit
import SwiftUI
import Testing
@testable import Unlimited
import UnlimitedKit

@MainActor
private func indicatorInk(_ factor: Double) throws -> NSBitmapImageRep {
    let indicator = SteeringIndicator(routes: [.init(id: "route", target: "openai", multiplier: factor,
                                                    until: .distantFuture)], now: .now)
    let renderer = ImageRenderer(content: SteeringIndicatorView(indicator: indicator)
        .frame(width: 20, height: 20).background(.black))
    renderer.scale = 2
    return NSBitmapImageRep(cgImage: try #require(renderer.cgImage))
}

@MainActor
@Test func hundredfoldArrowStacksStayInsideTheExistingIconHeight() throws {
    for factor in [10.0, 100, 0.01] {
        let image = try indicatorInk(factor)
        var rows: [Int] = []
        for y in 0..<image.pixelsHigh {
            for x in 0..<image.pixelsWide {
                let c = try #require(image.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
                if max(c.redComponent, c.greenComponent, c.blueComponent) > 0.2 { rows.append(y) }
            }
        }
        let height = try #require(rows.max()) - #require(rows.min()) + 1
        #expect(height <= 16)
    }
}
