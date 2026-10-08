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
}

struct SettingsView: View {
    @ObservedObject var model: StripModel
    @StateObject private var state = SettingsState()

    var body: some View {
        Form {
            Section {
                ForEach(ordered) { AccountRow(model: model, tile: $0) }
            } header: {
                Text("Accounts")
            } footer: {
                VStack(alignment: .leading, spacing: 4) {
                    if model.steeringPending { ProgressView().controlSize(.small) }
                    if let problem = model.offerProblem { Text(problem).foregroundStyle(.red) }
                    if let problem = model.steeringProblem { Text(problem).foregroundStyle(.red) }
                    if !model.accounts.isEmpty {
                        if !model.canOffer {
                            Text("Bans unavailable: unlimited did not answer off --json. Upgrade the CLI or check unlimited off.")
                        }
                        if !model.canSteer {
                            Text("Multipliers unavailable: unlimited did not answer incentive --json.")
                        }
                    }
                }
                .font(.caption)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            if !otherIncentives.isEmpty || !unmatchedOffers.isEmpty {
                Section("Other policies") {
                    ForEach(otherIncentives) { StoredPolicyRow(model: model, group: $0) }
                    ForEach(unmatchedOffers) { row in
                        HStack {
                            Text(row.account.map { "\(row.target) · \($0)" } ?? row.target)
                            Text("not read here").foregroundStyle(.secondary)
                            Spacer()
                            Button("Allow") { model.offer(row.target, account: row.account, false) }
                                .disabled(!model.canOffer || model.steeringPending)
                        }
                    }
                }
            }
            Section("General") {
                Toggle("Auto-hide normal accounts", isOn: Binding(
                    get: { model.prefs.autoHideNormal },
                    set: { value in model.arrange { $0.autoHideNormal = value } }))
                    .help("Hide normal menu-bar accounts until hover. Accounts hidden with the eye stay hidden; tracking is unchanged.")
                Toggle("Open at login", isOn: Binding(get: { state.atLogin }, set: { setLogin($0) }))
                if let e = state.loginError { Text(e).font(.caption).foregroundStyle(.red) }
                TextField("unlimited path", text: $state.path, prompt: Text("found automatically"))
                    .disabled(model.steeringPending)
                    .onSubmit { model.customPath = state.path }
            }
        }
        .formStyle(.grouped)
        // A grouped form scrolls and reports no height of its own; without one the window
        // opens as a bare title bar.
        .frame(width: 600, height: 680)
        .onAppear { state.path = model.customPath }
    }

    private var unmatchedOffers: [Tile.OfferRow] {
        Tile.offerRows(readings: Array(model.readings.values), switches: model.offSwitches).filter { !$0.readHere }
    }

    private var otherIncentives: [Runner.Incentive] {
        model.incentives.filter { group in
            !model.readings.values.contains { reading in
                reading.account != nil && group.target == reading.vendor && group.account == reading.account &&
                    SteeringDraft.group(target: reading.vendor, account: reading.account, groups: [group]) != nil
            }
        }
    }

    private var ordered: [Tile] {
        let rank = Dictionary(model.prefs.order.enumerated().map { ($1, $0) }, uniquingKeysWith: { a, _ in a })
        return model.accounts.sorted { (rank[$0.id] ?? .max) < (rank[$1.id] ?? .max) }
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
final class StoredPolicyState: ObservableObject {
    @Published var editing = false
}

struct StoredPolicyRow: View {
    @ObservedObject var model: StripModel
    let group: Runner.Incentive
    @StateObject private var state = StoredPolicyState()

    private var title: String { group.target + (group.account.map { " · \($0)" } ?? "") }

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(SteeringIndicator.format(group.multiplier) + " · " +
                     (group.until.map { ISO8601DateFormatter().string(from: $0) } ?? "until each account resets"))
                    .font(.caption).foregroundStyle(.secondary)
                    .help(group.bindings.map {
                        "\($0.vendor)/\($0.account) · \(ISO8601DateFormatter().string(from: $0.until))"
                    }.joined(separator: "\n"))
            }
            Spacer()
            Button("Edit") { state.editing = true }
                .popover(isPresented: $state.editing) {
                    VStack(alignment: .leading, spacing: 12) {
                        Text(title).font(.headline)
                        SteeringEditor(model: model, target: group.target, account: group.account)
                    }
                    .padding(16)
                    .frame(width: 280)
                }
            Button("Clear") { model.steer(target: group.target, multiplier: "off", account: group.account) }
        }
        .disabled(!model.canSteer || model.steeringPending)
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
