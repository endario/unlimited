import SwiftUI
import UnlimitedKit

extension Health {
    /// Neutral is the menu bar's own colour: nothing to act on.
    var color: Color {
        switch self {
        case .useMore: .green
        case .normal: .primary
        case .amber: .orange
        case .red: .red
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
    let tile: Tile
    /// Whether the strip is in the phase that shows each tile's alternate window.
    let alternating: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let showAlt = alternating && tile.alternate != nil && !reduceMotion
        VStack(spacing: -1) {
            Text(tile.label)
                .font(.system(size: 8, weight: .medium, design: .monospaced))
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
        .frame(width: 26)
    }
}

struct StripView: View {
    @ObservedObject var model: StripModel

    var body: some View {
        HStack(spacing: 3) {
            ForEach(model.tiles) { TileView(tile: $0, alternating: model.alternating) }
        }
        .padding(.horizontal, 4)
        .frame(height: 22)
    }
}
