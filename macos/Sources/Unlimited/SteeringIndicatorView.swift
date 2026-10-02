import SwiftUI
import UnlimitedKit

struct SteeringIndicatorView: View {
    let indicator: SteeringIndicator?

    var body: some View {
        if let indicator {
            HStack(spacing: 2) {
                if let up = indicator.up { mark(up, down: false, color: Health.underUsed.color) }
                if let down = indicator.down { mark(down, down: true, color: Health.red.color) }
            }
            .fixedSize()
            .help(indicator.tooltip)
            .accessibilityLabel(indicator.tooltip)
        }
    }

    private func mark(_ mark: SteeringIndicator.Mark, down: Bool, color: Color) -> some View {
        FastForward(heads: mark.triangles, down: down)
            .fill(color)
            .frame(width: 5, height: 8)
    }
}

private struct FastForward: Shape {
    let heads: Int
    let down: Bool

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let gap = rect.height * 0.08
        let height = (rect.height - 2 * gap) / 3
        let total = CGFloat(heads) * height + CGFloat(max(heads - 1, 0)) * gap
        for i in 0..<heads {
            let y = rect.midY - total / 2 + CGFloat(i) * (height + gap)
            path.move(to: CGPoint(x: rect.midX, y: down ? y + height : y))
            path.addLine(to: CGPoint(x: rect.minX, y: down ? y : y + height))
            path.addLine(to: CGPoint(x: rect.maxX, y: down ? y : y + height))
            path.closeSubpath()
        }
        return path
    }
}
