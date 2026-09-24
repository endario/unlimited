import SwiftUI
import UnlimitedKit

struct PopoverView: View {
    @ObservedObject var model: StripModel

    private var tile: Tile? { model.tiles.first { $0.id == model.selected } ?? model.tiles.first }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let why = model.problem {
                Text(why).font(.callout).foregroundStyle(.secondary)
                footer(nil)
            } else if let tile, let reading = model.readings[tile.id] {
                header(tile, reading)
                ForEach(Card.cards(reading, now: Date())) { CardView(card: $0) }
                if let credits = Card.credits(reading) {
                    Box { Label(credits, systemImage: "creditcard").font(.callout) }
                }
                footer(reading)
            } else {
                Text(model.accounts.isEmpty ? "Reading…" : "Every account is hidden: see Settings.")
                    .foregroundStyle(.secondary)
                footer(nil)
            }
        }
        .padding(8)
        .frame(width: 320)
    }

    /// Which account this is: the strip above is the tabs, so the popover only names it.
    private func header(_ t: Tile, _ r: Reading) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Text(t.label).font(.headline.monospaced())
            Text(([Tile.vendorName(t.vendor)] + r.names).joined(separator: " · "))
                .font(.callout).foregroundStyle(.secondary)
            Spacer()
            if t.best { Text("best pick").font(.caption).foregroundStyle(.secondary) }
        }
    }

    /// Refresh and Quit are always here: the app has no Dock icon or menu to quit from.
    private func footer(_ r: Reading?) -> some View {
        HStack {
            if let r {
                VStack(alignment: .leading, spacing: 2) {
                    if let plan = Card.plan(r) { Text(plan) }
                    // Only a stale reading is worth a word.
                    if let at = r.takenAt, Date().timeIntervalSince(at) > Tile.staleAfter {
                        Label("Read \(Card.until(Date(), at)) ago", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(Health.amber.color)
                    }
                }
            }
            Spacer()
            Button { model.refresh(maxAge: 0) } label: { Image(systemName: "arrow.clockwise") }
                .buttonStyle(.plain).help("Read now")
            Button { model.closePopover(); SettingsWindow.show(model) } label: { Image(systemName: "gearshape") }
                .buttonStyle(.plain).help("Settings")
            Button { NSApp.terminate(nil) } label: { Image(systemName: "power") }
                .buttonStyle(.plain).help("Quit")
        }
        .font(.caption)
    }
}

struct Box<Content: View>: View {
    @ViewBuilder let content: Content
    var body: some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 10)
            .padding(.horizontal, 8)
            .background(Color.primary.opacity(0.06), in: .rect(cornerRadius: 10))
    }
}

struct CardView: View {
    let card: Card
    static let barHeight: CGFloat = 4

    var body: some View {
        Box {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(card.title).font(.callout.weight(.semibold))
                    Spacer()
                    Text(card.used.map(percent) ?? "?")
                        .font(.callout.weight(.semibold)).monospacedDigit()
                        .foregroundStyle(card.health.color)
                }
                bar
                if let resets = card.resets { Text(resets).font(.caption).foregroundStyle(.secondary) }
                if card.momentum != nil || card.projected != nil || card.runsOut != nil {
                    HStack(spacing: 10) {
                        if let m = card.momentum { Label(percent(m), systemImage: "bolt.fill").help("At today's pace, by reset") }
                        if let p = card.projected {
                            Label(percent(p), systemImage: "chart.line.uptrend.xyaxis").help("Forecast at reset")
                        }
                        if let r = card.runsOut { Text(r) }
                        if let o = card.odds { Text("\(o)% chance").help("Of running out, from past windows") }
                    }
                    .font(.caption).monospacedDigit()
                    // Italic: a guess from this window's paces alone, before past windows back it.
                    .italic(!card.fromHistory)
                    .foregroundStyle(card.health == .normal ? Color.secondary : card.health.color)
                }
            }
        }
    }

    private func percent(_ v: Double) -> String { "\(Int((v * 100).rounded()))%" }

    /// Used so far, a tick where an even pace would be by now, and where it is heading: today's
    /// pace marked above the bar, the forecast below. Past 100% a mark only just bleeds out.
    private var bar: some View {
        GeometryReader { g in
            let w = g.size.width
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 1.5).fill(Color.primary.opacity(0.12))
                // Neutral is grey: the accent colour is often blue, which here means a sprint.
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(card.health == .normal ? Color.primary.opacity(0.45) : card.health.color)
                    .frame(width: w * min(card.used ?? 0, 1))
                Rectangle().fill(Color.primary.opacity(0.6)).frame(width: 1, height: Self.barHeight + 4)
                    .offset(x: w * card.elapsed - 0.5)
                if let m = card.momentum { arrow(down: true).offset(x: Card.marker(m, width: w) - 3, y: -(Self.barHeight + 3)) }
                if let p = card.projected { arrow(down: false).offset(x: Card.marker(p, width: w) - 3, y: Self.barHeight + 3) }
            }
        }
        .frame(height: Self.barHeight)
        .padding(.vertical, 5)
    }

    private func arrow(down: Bool) -> some View {
        Image(systemName: down ? "arrowtriangle.down.fill" : "arrowtriangle.up.fill")
            .font(.system(size: 6)).frame(width: 6)
            .foregroundStyle(card.health == .normal ? Color.primary.opacity(0.7) : card.health.color)
    }
}
