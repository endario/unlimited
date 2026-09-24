import SwiftUI
import UnlimitedKit

struct PopoverView: View {
    @ObservedObject var model: StripModel

    private var tile: Tile? { model.tiles.first { $0.id == model.selected } ?? model.tiles.first }
    private var vendors: [String] {
        model.tiles.map(\.vendor).reduce(into: []) { if !$0.contains($1) { $0.append($1) } }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let why = model.problem {
                Text(why).font(.callout).foregroundStyle(.secondary)
                footer(nil)
            } else if let tile, let reading = model.readings[tile.id] {
                tabs(current: tile.vendor)
                if model.tiles.filter({ $0.vendor == tile.vendor }).count > 1 { chips(vendor: tile.vendor) }
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

    private func tabs(current: String) -> some View {
        HStack(spacing: 4) {
            ForEach(vendors, id: \.self) { v in
                Button(Tile.vendorTab(v)) { model.selected = model.tiles.first { $0.vendor == v }?.id }
                    .help(Tile.vendorName(v))
                    .buttonStyle(.plain)
                    .font(.callout.weight(v == current ? .semibold : .regular))
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(v == current ? Color.primary.opacity(0.1) : .clear, in: .rect(cornerRadius: 6))
            }
        }
    }

    private func chips(vendor: String) -> some View {
        HStack(spacing: 6) {
            ForEach(model.tiles.filter { $0.vendor == vendor }) { t in
                Button { model.selected = t.id } label: {
                    HStack(spacing: 4) {
                        Circle().fill(t.health.color).frame(width: 6, height: 6)
                        Text("\(t.label) \(t.value.text)").monospacedDigit()
                    }
                }
                .buttonStyle(.plain)
                .font(.caption)
                .padding(.horizontal, 6).padding(.vertical, 3)
                .background(t.id == tile?.id ? Color.primary.opacity(0.1) : .clear, in: .rect(cornerRadius: 5))
                .opacity(t.dimmed ? 0.5 : 1)
            }
        }
    }

    /// Refresh and Quit are always here: the app has no Dock icon or menu to quit from.
    private func footer(_ r: Reading?) -> some View {
        HStack {
            if let r {
                VStack(alignment: .leading, spacing: 2) {
                    Text([Card.plan(r), r.names.joined(separator: ", ")].compactMap { $0 }.filter { !$0.isEmpty }
                            .joined(separator: " · "))
                    if let at = r.takenAt {
                        Text(Date().timeIntervalSince(at) < 60 ? "Read just now" : "Read \(Card.until(Date(), at)) ago")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
            Button { model.refresh(maxAge: 0) } label: { Image(systemName: "arrow.clockwise") }
                .buttonStyle(.plain).help("Read now")
            Button { SettingsWindow.show(model) } label: { Image(systemName: "gearshape") }
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
                Capsule().fill(card.health == .normal ? Color.accentColor : card.health.color)
                    .frame(width: g.size.width * min(card.used ?? 0, 1))
                Rectangle().fill(Color.primary.opacity(0.6)).frame(width: 1.5, height: 10)
                    .offset(x: g.size.width * card.elapsed - 0.75)
            }
        }
        .frame(height: 6)
    }
}
