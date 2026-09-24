import SwiftUI
import UnlimitedKit

extension Health {
    /// One palette for the strip, the text, the bars and the marks. Light tints: saturated
    /// colours sit at the dark card's own brightness and read poorly on it (each tint measured
    /// at 4.5:1 or better against a card). Neutral is the system's own foreground.
    var color: Color {
        switch self {
        case .sprint: Color(red: 0.62, green: 0.78, blue: 1)
        case .underUsed: Color(red: 0.56, green: 0.9, blue: 0.62)
        case .normal: .primary
        case .amber: Color(red: 1, green: 0.78, blue: 0.47)
        case .red: Color(red: 1, green: 0.68, blue: 0.66)
        }
    }
}

extension Tile.Alternate {
    var glyph: String {
        switch role {
        case "session": "clock"
        case "weekly_model": "bookmark.fill"
        case "month": "calendar"
        default: "circle.fill"
        }
    }
}

struct TileView: View {
    static let width: CGFloat = 26
    let tile: Tile
    /// The account the open popover shows.
    var focused = false
    /// Whether the strip is in the phase that shows each tile's alternate window.
    let alternating: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let showAlt = alternating && tile.alternate != nil && !reduceMotion
        VStack(spacing: -1) {
            Text(tile.label)
                .font(.system(size: 8, weight: .medium, design: .monospaced))
                .underline(tile.best, color: .primary.opacity(0.7))
            ZStack {
                Text(tile.value.text)
                    .foregroundStyle(tile.health.color)
                    .opacity(showAlt ? 0 : 1)
                if let alt = tile.alternate {
                    HStack(spacing: 1) {
                        Image(systemName: alt.glyph).font(.system(size: 6, weight: .bold))
                        Text(alt.value.text)
                    }
                    .foregroundStyle(alt.health.color)
                    .opacity(showAlt ? 1 : 0)
                }
            }
            .font(.system(size: 11, weight: .semibold, design: .rounded))
            .monospacedDigit()
        }
        // With Reduce Motion there is no cross-fade: a corner dot says another window is worse.
        .overlay(alignment: .topTrailing) {
            if reduceMotion, let alt = tile.alternate {
                Circle().fill(alt.health.color).frame(width: 3, height: 3)
            }
        }
        .opacity(tile.dimmed ? 0.45 : 1)
        .frame(width: Self.width, height: 22)
        .background(focused ? Color.primary.opacity(0.18) : .clear, in: .rect(cornerRadius: 4))
    }
}

struct StripView: View {
    static let spacing: CGFloat = 3, padding: CGFloat = 2
    @ObservedObject var model: StripModel

    var body: some View {
        HStack(spacing: Self.spacing) {
            ForEach(model.tiles) { t in
                TileView(tile: t, focused: model.open && t.id == model.selected, alternating: model.alternating)
            }
        }
        .padding(.horizontal, Self.padding)
        .frame(height: 22)
    }
}
