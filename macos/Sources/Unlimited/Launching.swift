import AppKit
import UnlimitedKit

extension Launch {
    func openEditor(_ bundle: URL) {
        NSWorkspace.shared.openApplication(at: bundle, configuration: .init())
    }

    /// Typed into a new iTerm window, so the login shell supplies the `PATH` the wrapper needs:
    /// an app launched from the menu bar has none.
    func openTerminal() {
        let command = "tmux new-session '\(cli.path)'"
        let script = """
            tell application "iTerm"
                activate
                set w to (create window with default profile)
                tell current session of w to write text "\(command)"
            end tell
            """
        var error: NSDictionary?
        NSAppleScript(source: script)?.executeAndReturnError(&error)
        if let error { NSLog("Unlimited: iTerm launch failed: %@", error) }
    }
}
