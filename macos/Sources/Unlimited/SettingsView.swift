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
                        Text(([Tile.vendorName(t.vendor)] + (model.readings[t.id]?.names ?? [])).joined(separator: " · "))
                            .foregroundStyle(.secondary)
                        Spacer()
                        Button { move(t, by: -1) } label: { Image(systemName: "chevron.up") }.buttonStyle(.borderless)
                        Button { move(t, by: 1) } label: { Image(systemName: "chevron.down") }.buttonStyle(.borderless)
                    }
                }
            }
            Section {
                if model.canOffer {
                    ForEach(Tile.offerRows(readings: Array(model.readings.values), switches: model.offSwitches)) { row in
                        HStack {
                            Toggle("", isOn: Binding(get: { row.off },
                                                     set: { v in model.offer(row.target, account: row.account, v) }))
                                .labelsHidden()
                            Text("\(Tile.vendorName(row.vendor)) · \(row.ident)")
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
                    .onSubmit { model.customPath = state.path }
            }
        }
        .formStyle(.grouped)
        // A grouped form scrolls and reports no height of its own; without one the window
        // opens as a bare title bar.
        .frame(width: 460, height: 520)
        .onAppear { state.path = model.customPath }
    }

    /// Never offer vs the Accounts toggles above (hidden ≠ never spent), the binary the app
    /// would write through, and the two ways the section cannot work.
    @ViewBuilder private var neverOfferFooter: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let p = model.offerProblem {
                Text(p).foregroundStyle(.red)
            }
            if model.canOffer {
                Text("Off = no choice here spends the account (a whole-vendor switch, written by the CLI, spends every account of the vendor); usage is still tracked. The Accounts toggles above only hide a tile.")
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
