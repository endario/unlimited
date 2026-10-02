import AppKit
import ServiceManagement
import SwiftUI
import UnlimitedKit

/// The window's own state. (`@State` is a macro that Command Line Tools cannot expand.)
@MainActor
final class SettingsState: ObservableObject {
    @Published var atLogin = SMAppService.mainApp.status == .enabled
    @Published var loginError: String?
    @Published var path = ""
    @Published var target = ""
    @Published var account = ""
    @Published var multiplier = "10x"
    @Published var duration = "12h"
    @Published var untilReset = true
}

struct SettingsView: View {
    @ObservedObject var model: StripModel
    @StateObject private var state = SettingsState()
    private let presets = ["2x", "5x", "10x", "1x", "0.5x", "0.2x", "0.1x"]

    var body: some View {
        Form {
            Section("Accounts") {
                ForEach(ordered) { t in
                    HStack {
                        Toggle("", isOn: Binding(get: { !model.prefs.hidden.contains(t.id) },
                                                 set: { v in model.arrange { $0.hide(t.id, !v) } }))
                            .labelsHidden()
                        TextField("Label", text: Binding(get: { model.prefs.labels[t.id] ?? t.label },
                                                         set: { v in model.arrange { $0.rename(t.id, to: v) } }))
                            .labelsHidden()
                            .frame(width: 60)
                            .font(.system(.body, design: .monospaced))
                        SteeringIndicatorView(indicator: t.indicator)
                        Text(([Tile.vendorName(t.vendor)] + (model.readings[t.id]?.names ?? [])).joined(separator: " · "))
                            .foregroundStyle(.secondary)
                        Spacer()
                        Button("Steer") { select(target: t.vendor, account: model.readings[t.id]?.account) }
                            .buttonStyle(.borderless)
                            .accessibilityLabel("Steer \(t.label)")
                        Button { move(t, by: -1) } label: { Image(systemName: "chevron.up") }.buttonStyle(.borderless)
                        Button { move(t, by: 1) } label: { Image(systemName: "chevron.down") }.buttonStyle(.borderless)
                    }
                }
            }
            steeringSection
            Section {
                if model.canOffer {
                    ForEach(Tile.offerRows(readings: Array(model.readings.values), switches: model.offSwitches)) { row in
                        HStack {
                            Toggle("", isOn: Binding(get: { row.off },
                                                     set: { v in model.offer(row.target, account: row.account, v) }))
                                .labelsHidden()
                                .disabled(model.steeringPending)
                            // An unmatched whole-target switch is its own name: no double print.
                            Text(row.readHere || row.account != nil
                                 ? "\(Tile.vendorName(row.vendor)) · \(row.ident)" : row.ident)
                            if !row.readHere { Text("not read here").foregroundStyle(.secondary) }
                            Spacer()
                        }
                    }
                }
            } header: {
                Text("Never offer")
            } footer: {
                neverOfferFooter
            }
            Section("General") {
                Toggle("Open at login", isOn: Binding(get: { state.atLogin }, set: setLogin))
                if let e = state.loginError { Text(e).font(.caption).foregroundStyle(.red) }
                TextField("unlimited path", text: $state.path, prompt: Text("found automatically"))
                    .disabled(model.steeringPending)
                    .onSubmit { model.customPath = state.path }
            }
        }
        .formStyle(.grouped)
        // A grouped form scrolls and reports no height of its own; without one the window
        // opens as a bare title bar.
        .frame(width: 520, height: 680)
        .onAppear { state.path = model.customPath }
    }

    @ViewBuilder private var steeringSection: some View {
        Section("Steering") {
            if model.canSteer {
                Menu("Choose a vendor") {
                    ForEach(Array(Set(ordered.map(\.vendor))).sorted(), id: \.self) { vendor in
                        Button(Tile.vendorName(vendor)) { select(target: vendor, account: nil) }
                    }
                }
                Text("Or use Steer beside an account; custom model and route targets work below.")
                    .font(.caption).foregroundStyle(.secondary)
                TextField("Vendor, model, or route target", text: $state.target)
                    .accessibilityLabel("Settings target")
                TextField("Account (optional)", text: $state.account)
                    .accessibilityLabel("Settings account")
                Picker("Multiplier", selection: $state.multiplier) {
                    ForEach(presets, id: \.self) { factor in
                        Text(factor + (factor == "1x" ? " neutral" : Double(factor.dropLast())! > 1 ? " encourage" : " discourage"))
                            .tag(factor)
                    }
                    if !presets.contains(state.multiplier) {
                        Text("Custom").tag(state.multiplier)
                    }
                }
                .accessibilityLabel("Multiplier")
                TextField("Custom multiplier", text: $state.multiplier)
                    .accessibilityLabel("Custom multiplier")
                Toggle("Until reset", isOn: $state.untilReset)
                    .accessibilityLabel("Until reset")
                if !state.untilReset {
                    TextField("Duration, e.g. 12h", text: $state.duration)
                        .accessibilityLabel("Duration")
                }
                HStack {
                    Button("Apply") { apply() }
                        .disabled(state.target.isEmpty || model.steeringPending)
                        .accessibilityLabel("Apply")
                    Button("Clear") { clear() }
                        .disabled(state.target.isEmpty || model.steeringPending)
                        .accessibilityLabel("Clear")
                    if model.steeringPending { ProgressView().controlSize(.small) }
                }
                Text("Local to this machine. Reset/account steering needs choose --account OFFERING=ACCOUNT (or rank accounts=). Clear removes this scope; inherited settings may still apply.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(Array(model.incentives.enumerated()), id: \.offset) { _, group in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(group.target + (group.account.map { " · \($0)" } ?? ""))
                            Text(SteeringIndicator.format(group.multiplier) + " · " +
                                 (group.until.map { ISO8601DateFormatter().string(from: $0) } ?? "until each account resets"))
                                .font(.caption).foregroundStyle(.secondary)
                            ForEach(group.bindings) { binding in
                                Text("\(binding.names.first ?? binding.account) · \(ISO8601DateFormatter().string(from: binding.until))")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Spacer()
                        Button("Edit") { select(target: group.target, account: group.account) }
                        Button("Clear") { model.steer(target: group.target, multiplier: "off", account: group.account) }
                    }
                    .disabled(model.steeringPending)
                }
                if let problem = model.steeringProblem { Text(problem).foregroundStyle(.red) }
            } else {
                Text("Steering is unavailable until this unlimited CLI supports `incentive --json`.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func select(target: String, account: String?) {
        state.target = target
        state.account = account ?? ""
        if let group = model.incentives.first(where: { $0.target == target && $0.account == account }) {
            state.multiplier = SteeringIndicator.format(group.multiplier)
            state.untilReset = group.until == nil
            if let until = group.until {
                state.duration = "\(max(1, Int(ceil(until.timeIntervalSinceNow / 60))))m"
            }
        }
    }

    private func apply() {
        model.steer(target: state.target, multiplier: state.multiplier,
                    account: state.account.isEmpty ? nil : state.account,
                    duration: state.untilReset ? nil : state.duration)
    }

    private func clear() {
        model.steer(target: state.target, multiplier: "off", account: state.account.isEmpty ? nil : state.account)
    }

    /// Never offer vs the Accounts toggles above (hidden ≠ never spent), the binary the app
    /// would write through, and the two ways the section cannot work.
    @ViewBuilder private var neverOfferFooter: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let p = model.offerProblem {
                Text(p).foregroundStyle(.red)
            }
            if model.canOffer {
                Text("Off = no choice here spends the account (a whole-vendor switch, written by the CLI, spends every account of the vendor); usage is still tracked.")
                Text("Flips run through \(model.offerPath).")
            } else {
                Text("unlimited did not answer `off --json`: upgrade it (`uv tool install --force unlimited`) or repair what `unlimited off` reports.")
            }
        }
        .font(.caption)
    }

    private var ordered: [Tile] {
        let rank = Dictionary(model.prefs.order.enumerated().map { ($1, $0) }, uniquingKeysWith: { a, _ in a })
        return model.accounts.sorted { (rank[$0.id] ?? .max) < (rank[$1.id] ?? .max) }
    }

    private func move(_ t: Tile, by step: Int) {
        model.arrange { $0.step(t.id, by: step) }
    }

    private func setLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            state.loginError = nil
        } catch {
            state.loginError = error.localizedDescription
        }
        state.atLogin = SMAppService.mainApp.status == .enabled
    }
}

@MainActor
enum SettingsWindow {
    private static var window: NSWindow?

    static func show(_ model: StripModel) {
        if window == nil {
            let w = NSWindow(contentViewController: NSHostingController(rootView: SettingsView(model: model)))
            w.title = "Unlimited Settings"
            w.styleMask = [.titled, .closable]
            w.isReleasedWhenClosed = false
            window = w
        }
        NSApp.activate()
        window?.makeKeyAndOrderFront(nil)
    }
}
