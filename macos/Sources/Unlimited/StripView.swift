import SwiftUI
import UnlimitedKit

struct TileView: View {
    let tile: Tile

    var body: some View {
        VStack(spacing: -1) {
            Text(tile.label)
                .font(.system(size: 8, weight: .medium, design: .monospaced))
            Text(tile.value.text)
                .font(.system(size: 11, weight: .semibold, design: .rounded))
                .monospacedDigit()
        }
        .foregroundStyle(.primary)
        .opacity(tile.dimmed ? 0.45 : 1)
        .frame(width: 26)
    }
}

struct StripView: View {
    @ObservedObject var model: StripModel

    var body: some View {
        HStack(spacing: 3) {
            ForEach(model.tiles) { TileView(tile: $0) }
        }
        .padding(.horizontal, 4)
        .frame(height: 22)
    }
}
