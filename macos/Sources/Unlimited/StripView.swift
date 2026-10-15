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
        // Rose, not coral: coral sat too close to the amber. Same brightness, so the same contrast.
        case .red: Color(red: 1, green: 0.62, blue: 0.72)
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

struct UsageFigure: View {
    let value: Tile.Value
    let health: Health
    var percentSign = false

    var text: String { value.text + (percentSign && value.showsData ? "%" : "") }

    var body: some View {
        Text(text)
            .foregroundStyle(value.showsData ? health.color : Color.secondary)
            .monospacedDigit()
    }
}

struct TileView: View {
    static let valueFont = Font.system(size: 9, weight: .semibold, design: .rounded)
    static let width = CGFloat(StripLayout.neutralWidth)
    static func width(of tile: Tile) -> CGFloat {
        CGFloat(StripLayout(tiles: [tile], spacing: 0, padding: 0).width(of: tile))
    }
    let tile: Tile
    /// The account the open popover shows.
    var focused = false
    /// Whether the strip is in the phase that shows each tile's alternate window.
    let alternating: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let showAlt = alternating && tile.alternate != nil && !reduceMotion
        VStack(spacing: -1.5) {
            HStack(spacing: 1) {
                Text(tile.label)
                    .font(.system(size: 7, weight: .medium, design: .monospaced))
                    // Drawn just under the capitals, not at the font's underline position: that sits in
                    // the gap above the figure and makes this label's gap look smaller than its neighbours'.
                    .overlay(alignment: .bottom) {
                        if tile.best { Rectangle().fill(.primary.opacity(0.7)).frame(height: 0.5).offset(y: -1.25) }
                    }
                    .help(tile.best ? "Underline: best account by quota room, not steering" : "Account label")
                SteeringIndicatorView(indicator: tile.indicator)
            }
            ZStack {
                UsageFigure(value: tile.value, health: tile.health)
                    .opacity(showAlt ? 0 : 1)
                if let alt = tile.alternate {
                    HStack(spacing: 1) {
                        Image(systemName: alt.glyph).font(.system(size: 5, weight: .bold))
                        UsageFigure(value: alt.value, health: alt.health)
                    }
                    .foregroundStyle(alt.value.showsData ? alt.health.color : Color.secondary)
                    .opacity(showAlt ? 1 : 0)
                }
            }
            .font(Self.valueFont)
            .monospacedDigit()
        }
        // With Reduce Motion there is no cross-fade: a corner dot says another window is worse.
        .overlay(alignment: .topTrailing) {
            if reduceMotion, let alt = tile.alternate {
                Circle().fill(alt.health.color).frame(width: 3, height: 3)
            }
        }
        // Ghosted, not recoloured: an unusable or switched-off tile keeps its exact colours
        // and drops to half transparency.
        .opacity(tile.dimmed || tile.off ? 0.45 : 1)
        // Room for the elapsed bar, so the text sits close beside it rather than under it.
        .padding(.trailing, tile.elapsed == nil ? 0 : 2)
        .frame(width: Self.width(of: tile), height: 22)
        // The week's time so far, filling upward to the reset.
        .overlay(alignment: .trailing) {
            if let e = tile.elapsed {
                ZStack(alignment: .bottom) {
                    Capsule().fill(Color.primary.opacity(0.15))
                    Capsule().fill(Color.primary.opacity(0.5)).frame(height: 16 * min(max(e, 0), 1))
                }
                .frame(width: 1.5, height: 16)
            }
        }
        .background(focused ? Color.primary.opacity(0.18) : .clear, in: .rect(cornerRadius: 4))
    }
}

struct StripView: View {
    static let spacing: CGFloat = 1, padding: CGFloat = 2
    static let fadeDuration = 0.7
    @ObservedObject var model: StripModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: Self.spacing) {
            ForEach(model.displayedTiles) { t in
                if model.collapsedNormal {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 12, weight: .medium))
                        .frame(width: TileView.width, height: 22)
                        .help("Normal accounts hidden automatically. Hover to show; click to open.")
                        .accessibilityLabel("\(model.tiles.count) normal accounts hidden. Show accounts.")
                } else {
                    TileView(tile: t, focused: model.open && t.id == model.selected, alternating: model.alternating)
                        .opacity(model.fades(t) ? 0 : 1)
                        .animation(reduceMotion ? nil : .easeInOut(duration: Self.fadeDuration), value: model.fadingNormal)
                        .transition(reduceMotion || !t.isRoutine ? .identity : .opacity)
                }
            }
        }
        .padding(.horizontal, Self.padding)
        .frame(height: 22)
    }
}
