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
            }
            Section("General") {
                Toggle("Auto-hide normal accounts", isOn: Binding(
                    get: { model.prefs.autoHideNormal },
                    set: { value in model.arrange { $0.autoHideNormal = value } }))
                    .help("Hide routine menu-bar accounts until hover, including amber ones. Accounts hidden with the eye stay hidden; tracking is unchanged.")
                Toggle("Open at login", isOn: Binding(get: { state.atLogin }, set: { setLogin($0) }))
                if let e = state.loginError { Text(e).font(.caption).foregroundStyle(.red) }
                TextField("unlimited path", text: $state.path, prompt: Text("found automatically"))
                    .onSubmit { model.customPath = state.path }
            }
        }
        .formStyle(.grouped)
        // A grouped form scrolls and reports no height of its own; without one the window
        // opens as a bare title bar.
        .frame(width: 600, height: 680)
        .onAppear { state.path = model.customPath }
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
