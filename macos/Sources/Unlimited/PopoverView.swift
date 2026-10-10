import SwiftUI
import UnlimitedKit

@MainActor
final class PopoverState: ObservableObject {
    @Published var launchProblem: String?
    @Published var launching = false
}

struct PopoverView: View {
    @ObservedObject var model: StripModel
    @StateObject private var state = PopoverState()

    private var tile: Tile? { model.tiles.first { $0.id == model.selected } ?? model.tiles.first }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let why = model.problem {
                Text(why).font(.callout).foregroundStyle(.secondary)
                footer(nil)
            } else if let tile, let reading = model.readings[tile.id] {
                header(tile, reading)
                if let issue = reading.issue(now: Date()) {
                    UsageIssueView(issue: issue, login: issue.offersLogin ? model.launches[tile.id].map { launch in
                        { openTerminal(launch, command: launch.loginCommand) }
                    } : nil)
                    .disabled(state.launching)
                    .help(reading.why ?? issue.title)
                }
                if let launchProblem = state.launchProblem {
                    Text(launchProblem).font(.callout)
                        .fixedSize(horizontal: false, vertical: true)
                }
                ForEach(Card.cards(reading, now: Date())) { CardView(card: $0) }
                if let credits = Card.credits(reading) {
                    Box { Label(credits, systemImage: "creditcard").font(.callout) }
                        .help("Extra usage credits: whether the account may spend past its limits")
                }
                if reading.vendor == "anthropic" {
                    let api = ApiCredit.linked(to: reading, in: Array(model.readings.values))
                    if let api, api.status == "ok" { ForEach(Card.cards(api, now: Date())) { CardView(card: $0) } }
                    ApiCreditView(linked: api != nil, none: api?.why == "no-api-organization",
                                  line: api.flatMap { ApiCredit.line($0, now: Date()) },
                                  profile: ApiCredit.profile(for: reading, linked: api))
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
        .onChange(of: tile?.id) { state.launchProblem = nil }
        .onChange(of: tile?.value) { state.launchProblem = nil }
    }

    /// Which account this is: the strip above is the tabs, so the popover only names it.
    private func header(_ t: Tile, _ r: Reading) -> some View {
        HStack(alignment: .center, spacing: 6) {
            DescribedAccount(description: ([Tile.vendorName(t.vendor)] + r.names).joined(separator: " · "),
                             help: "The account shown: pick another on the strip", reading: r)
                .font(.callout).foregroundStyle(.secondary)
            SteeringIndicatorView(indicator: t.indicator)
            Spacer()
            // The star is drawn taller than the rectangles beside it: 11.25pt medium matches their ink height and stroke.
            if t.best { Image(systemName: "star").font(.system(size: 11.25, weight: .medium)).help("Best pick") }
            if let launch = model.launches[t.id] {
                if let editor = launch.editor {
                    Button { model.closePopover(); launch.openEditor(editor) } label: { Image(systemName: "text.rectangle") }
                        .buttonStyle(.plain).help("Open a VS Code session as \(t.label)")
                }
                Button { openTerminal(launch, closeOnSuccess: true) } label: { Image(systemName: "terminal") }
                    .buttonStyle(.plain).disabled(state.launching).help("Open a CLI session in tmux as \(t.label)")
            }
        }
    }

    private func openTerminal(_ launch: Launch, command: String? = nil, closeOnSuccess: Bool = false) {
        guard !state.launching else { return }
        let account = tile?.id
        state.launching = true
        state.launchProblem = nil
        Task {
            let problem = await launch.openTerminal(command: command)
            state.launching = false
            guard tile?.id == account else { return }
            state.launchProblem = problem
            if problem == nil && closeOnSuccess { model.closePopover() }
        }
    }

    /// Refresh and Quit are always here: the app has no Dock icon or menu to quit from.
    private func footer(_ r: Reading?) -> some View {
        HStack {
            if let r {
                VStack(alignment: .leading, spacing: 2) {
                    if let plan = Card.plan(r) { Text(plan).help("The account's plan") }
                    // Only a stale reading is worth a word.
                    if let at = r.takenAt, Date().timeIntervalSince(at) > Tile.staleAfter {
                        Label("Read \(Card.until(Date(), at)) ago", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(Health.amber.color)
                            .help("This reading is old: no newer one has come in")
                    }
                }
            }
            Spacer()
            Button { model.refresh(maxAge: 0) } label: { Image(systemName: "arrow.clockwise") }
                .buttonStyle(.plain).help("Read now")
            Button { model.closePopover(); SettingsWindow.show(model) } label: { Image(systemName: "gearshape") }
                .buttonStyle(.plain).help("Settings").accessibilityLabel("Settings")
            Button { NSApp.terminate(nil) } label: { Image(systemName: "power") }
                .buttonStyle(.plain).help("Quit")
        }
        .font(.caption)
    }
}

/// The Console's balance for this login, or the way to sign in to it: lapsed when linked, never yet
/// when not.
struct ApiCreditView: View {
    let linked: Bool
    let none: Bool
    let line: String?
    let profile: String

    var body: some View {
        Box {
            if let line {
                Label(line, systemImage: "dollarsign.circle").font(.callout)
                    .help("As the Claude Console reports it, read on Chrome profile \(profile)")
            } else if none {
                Label("No API organization on this login", systemImage: "dollarsign.circle")
                    .font(.callout).foregroundStyle(.secondary)
            } else {
                HStack {
                    Label(linked ? "API credit: Console sign-in lapsed" : "API credit", systemImage: "dollarsign.circle")
                        .font(.callout).foregroundStyle(linked ? .primary : .secondary)
                    Spacer()
                    Button("Sign in", action: open)
                        .help("Sign in to the Claude Console in Chrome profile \(profile) as this account, then refresh")
                }
            }
        }
    }

    private func open() {
        // The reading takes the cookie from this Chrome profile; Chrome creates the profile if it is new.
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-na", "Google Chrome", "--args", "--profile-directory=\(profile)", ApiCredit.console.absoluteString]
        try? p.run()
    }
}

struct UsageIssueView: View {
    let issue: UsageIssue
    let login: (() -> Void)?

    var body: some View {
        Box {
            VStack(alignment: .leading, spacing: 8) {
                Text(issue.title).font(.callout.weight(.semibold))
                Text(issue.message).font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                if issue.offersLogin {
                    if let login {
                        Button(action: login) {
                            Text("\(Image(systemName: "terminal"))\u{2009}Sign in")
                        }
                        .help("Open iTerm to sign in to this account")
                        Text("Choose this account in the browser, then refresh.")
                            .font(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        Text("No account launcher was found in ~/.local/bin. Sign in through this account’s Claude Code setup, then refresh.")
                            .font(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
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
    static let barHeight: CGFloat = 5

    var body: some View {
        Box {
            // No stack spacing: the bar's own padding sets equal room above and below it.
            VStack(alignment: .leading, spacing: 0) {
                // Three columns on one baseline. The side columns take equal widths, so the
                // middle is centred on the card. Above the bar: today's pace, whose mark is above
                // it; below: the forecast, whose mark is below it.
                row {
                    Text(card.title).font(.callout.weight(.semibold)).help("The usage window")
                } middle: {
                    Text(card.used.map(percent) ?? "?")
                        .font(.callout.weight(.semibold)).foregroundStyle(card.health.color)
                        .help("Used so far this window")
                } right: {
                    if let m = card.momentum { guess(figure(m, "bolt.fill")).help("At today's pace, by reset") }
                }
                bar
                row {
                    HStack(spacing: 8) {
                        if let r = card.runsOut { guess(figure(r, "flame")).help("Runs out in \(r)") }
                        if let o = card.odds { guess(figure("\(o)%", "dice")).help("Chance of running out, from past windows") }
                    }
                } middle: {
                    if let resets = card.resets {
                        figure(resets, "arrow.counterclockwise").foregroundStyle(.secondary)
                            .help("Resets in \(resets)" + (card.resetsAt.map { ", \($0)" } ?? ""))
                    }
                } right: {
                    if let p = card.projected { guess(figure(p, "chart.line.uptrend.xyaxis")).help("Forecast at reset") }
                }
            }
        }
    }

    private func row<L: View, M: View, R: View>(@ViewBuilder _ left: () -> L, @ViewBuilder middle: () -> M,
                                                @ViewBuilder right: () -> R) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            left().frame(maxWidth: .infinity, alignment: .leading)
            middle()
            right().frame(maxWidth: .infinity, alignment: .trailing)
        }
        .font(.callout).monospacedDigit()
    }

    /// A forecast figure: its colour, and thinner than regular while it is a guess from this
    /// window's paces alone, before past windows back it.
    private func guess(_ t: Text) -> some View {
        t.fontWeight(card.fromHistory ? .regular : .light).foregroundStyle(tint)
    }

    /// An icon and its figure, closer than `Label` sets them.
    private func figure(_ v: Double, _ icon: String) -> Text { figure(percent(v), icon) }

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
            let w = g.size.width - 2 * Card.bleed  // the bar, inset at each end for the marks
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 1.5).fill(Color.primary.opacity(0.12)).frame(height: Self.barHeight)
                // Neutral is grey: the accent colour is often blue, which here means a sprint.
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(card.health == .normal ? Color.primary.opacity(0.45) : card.health.color)
                    .frame(width: w * min(card.used ?? 0, 1), height: Self.barHeight)
                Rectangle().fill(Color.primary.opacity(0.6)).frame(width: 2, height: (Self.barHeight + 4) * 2)
                    .offset(x: w * card.elapsed - 1)
                if let m = card.momentum { arrow(down: true).offset(x: Card.marker(m, width: w) - 3, y: -(Self.barHeight + 1)) }
                if let p = card.projected { arrow(down: false).offset(x: Card.marker(p, width: w) - 3, y: Self.barHeight + 1) }
            }
            // Sized to the bar, so the taller stripe and the marks overhang it evenly.
            .frame(width: w, height: Self.barHeight)
            .offset(x: Card.bleed)
        }
        .frame(height: Self.barHeight)
        .padding(.vertical, 12)
        .contentShape(Rectangle())
        .help("Filled: used so far. Line: where an even pace would be by now. ▼ today's pace at reset, ▲ the forecast at reset")
    }

    private func arrow(down: Bool) -> some View {
        Image(systemName: down ? "arrowtriangle.down.fill" : "arrowtriangle.up.fill")
            .font(.system(size: 6)).frame(width: 6)
            .foregroundStyle(card.health == .normal ? Color.primary.opacity(0.7) : card.health.color)
    }
}
