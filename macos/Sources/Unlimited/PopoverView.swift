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
        .padding(12)
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
                    if let at = r.takenAt {
                        Text(Date().timeIntervalSince(at) < 60 ? "Read just now" : "Read \(Card.until(Date(), at)) ago")
                            .foregroundStyle(.secondary)
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
            .padding(10)
            .background(Color.primary.opacity(0.06), in: .rect(cornerRadius: 10))
    }
}

struct CardView: View {
    let card: Card

    var body: some View {
        Box {
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(card.title).font(.callout.weight(.semibold))
                    Spacer()
                    Text(card.used.map { "\(Int(($0 * 100).rounded()))%" } ?? "?")
                        .font(.callout.weight(.semibold)).monospacedDigit()
                        .foregroundStyle(card.health.color)
                }
                bar
                if let resets = card.resets { Text(resets).font(.caption).foregroundStyle(.secondary) }
                if let forecast = card.forecast {
                    Text(forecast).font(.caption).foregroundStyle(card.health.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// Used so far, with a tick where an even pace would be by now.
    private var bar: some View {
        GeometryReader { g in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.primary.opacity(0.12))
                // Neutral is grey: the accent colour is often blue, which here means a sprint.
                Capsule().fill(card.health == .normal ? Color.primary.opacity(0.45) : card.health.color)
                    .frame(width: g.size.width * min(card.used ?? 0, 1))
                Rectangle().fill(Color.primary.opacity(0.6)).frame(width: 1.5, height: 10)
                    .offset(x: g.size.width * card.elapsed - 0.75)
            }
        }
        .frame(height: 6)
    }
}
