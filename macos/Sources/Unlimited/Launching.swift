import AppKit
import UnlimitedKit

extension Launch {
    func openEditor(_ bundle: URL) {
        NSWorkspace.shared.openApplication(at: bundle, configuration: .init())
    }

    /// Typed into a new iTerm window, so the login shell supplies the `PATH` the wrapper needs:
    /// an app launched from the menu bar has none.
    func openTerminal(command: String? = nil) async -> String? {
        let script = Self.terminalScript(command: command ?? terminalCommand)
        return await Task.detached(priority: .userInitiated) {
            do {
                _ = try Runner(binary: URL(filePath: "/usr/bin/osascript")).run(["-e", script], timeout: 20)
                return nil as String?
            } catch {
                NSLog("Unlimited: iTerm launch failed: %@", error as NSError)
                return "Could not open iTerm. Check that it is installed and allow Unlimited to control it in System Settings → Privacy & Security → Automation."
            }
        }.value
    }
}
