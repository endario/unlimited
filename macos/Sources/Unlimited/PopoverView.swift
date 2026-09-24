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
        HStack(alignment: .center, spacing: 6) {
            Text(([Tile.vendorName(t.vendor)] + r.names).joined(separator: " · "))
                .font(.callout).foregroundStyle(.secondary)
            Spacer()
            // The star is drawn taller than the rectangles beside it: 11.25pt medium matches their ink height and stroke.
            if t.best { Image(systemName: "star").font(.system(size: 11.25, weight: .medium)).help("Best pick") }
            // Placeholders: neither launches anything yet.
            Button {} label: { Image(systemName: "text.rectangle") }
                .buttonStyle(.plain).help("Open a VS Code session as \(t.label)")
            Button {} label: { Image(systemName: "terminal") }
                .buttonStyle(.plain).help("Open a CLI session in tmux as \(t.label)")
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
            // Darker than the popover, so coloured text stands off it by brightness.
            .background(Color.black.opacity(0.35), in: .rect(cornerRadius: 10))
    }
}

struct CardView: View {
    let card: Card
    static let barHeight: CGFloat = 3.5

    var body: some View {
        Box {
            // No stack spacing: the bar's own padding sets equal room above and below it.
            VStack(alignment: .leading, spacing: 0) {
                // One baseline: every text here is one font, so it also centres them exactly;
                // centring their boxes instead rounds each to a different half-pixel.
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(card.title).font(.callout.weight(.semibold))
                    Spacer()
                    Group {
                        if let m = card.momentum { figure(m, "bolt.fill").help("At today's pace, by reset") }
                        if let p = card.projected { figure(p, "chart.line.uptrend.xyaxis").help("Forecast at reset") }
                    }
                    .font(.callout.weight(.semibold)).monospacedDigit()
                    // Half a weight lighter: a guess from this window's paces alone, before past windows back it.
                    .fontWeight(card.fromHistory ? .semibold : .medium)
                    .foregroundStyle(tint)
                    Text(card.used.map(percent) ?? "?")
                        .font(.callout.weight(.semibold)).monospacedDigit()
                        .foregroundStyle(card.health.color)
                }
                bar
                if card.resets != nil || card.odds != nil || card.runsOut != nil {
                HStack(alignment: .firstTextBaseline) {
                    if let resets = card.resets {
                        figure(resets, "arrow.counterclockwise").foregroundStyle(.secondary).help(card.resetsAt ?? "")
                    }
                    Spacer()
                    Group {
                        if let o = card.odds { figure("\(o)%", "dice").help("Chance of running out, from past windows") }
                        if let r = card.runsOut { figure(r, "flame").help("Runs out in \(r)") }
                    }
                    .fontWeight(card.fromHistory ? .regular : .light)
                    .foregroundStyle(tint)
                }
                .font(.callout).monospacedDigit()
                }
            }
        }
    }

    /// An icon and its figure, closer than `Label` sets them.
    private func figure(_ v: Double, _ icon: String) -> some View { figure(percent(v), icon) }

    /// The symbol set inside the text run, not beside it: the typesetter places it on the
    /// digits' own baseline, so it cannot drift half a pixel from them as separate boxes did.
    private func figure(_ text: String, _ icon: String) -> Text {
        Text("\(Image(systemName: icon))\u{2009}\(text)")
    }

    private var tint: Color { card.health == .normal ? .secondary : card.health.color }

    private func percent(_ v: Double) -> String { "\(Int((v * 100).rounded()))%" }

    /// Used so far, a tick where an even pace would be by now, and where it is heading: today's
    /// pace marked above the bar, the forecast below. Past 100% a mark only just bleeds out.
    private var bar: some View {
        GeometryReader { g in
            let w = g.size.width
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 1.5).fill(Color.primary.opacity(0.12)).frame(height: Self.barHeight)
                // Neutral is grey: the accent colour is often blue, which here means a sprint.
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(card.health == .normal ? Color.primary.opacity(0.45) : card.health.color)
                    .frame(width: w * min(card.used ?? 0, 1), height: Self.barHeight)
                Rectangle().fill(Color.primary.opacity(0.6)).frame(width: 1, height: (Self.barHeight + 4) * 2)
                    .offset(x: w * card.elapsed - 0.5)
                if let m = card.momentum { arrow(down: true).offset(x: Card.marker(m, width: w) - 3, y: -(Self.barHeight + 2.5)) }
                if let p = card.projected { arrow(down: false).offset(x: Card.marker(p, width: w) - 3, y: Self.barHeight + 2.5) }
            }
            // Sized to the bar, so the taller stripe and the marks overhang it evenly.
            .frame(width: w, height: Self.barHeight)
        }
        .frame(height: Self.barHeight)
        .padding(.vertical, 12)
    }

    private func arrow(down: Bool) -> some View {
        Image(systemName: down ? "arrowtriangle.down.fill" : "arrowtriangle.up.fill")
            .font(.system(size: 6)).frame(width: 6)
            .foregroundStyle(card.health == .normal ? Color.primary.opacity(0.7) : card.health.color)
    }
}
