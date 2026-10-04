import Foundation

/// Where to open a Claude Code session as one account: a per-account wrapper in `~/.local/bin`
/// (`claude-N`, `claude-glm`, …) and, when installed, a VS Code app bundle whose launcher script
/// runs the matching `code-…` wrapper.
public struct Launch: Equatable, Sendable {
    /// The CLI wrapper, e.g. `~/.local/bin/claude-2`.
    public let cli: URL
    /// The VS Code bundle for this account, when one is installed.
    public let editor: URL?

    /// `accountN` runs as `claude-N`; a `claude-…` name is its wrapper already.
    static func wrapper(_ names: [String]) -> String? {
        for n in names {
            if n.hasPrefix("claude-") { return n }
            if n.hasPrefix("account") {
                let suffix = n.dropFirst("account".count)
                if !suffix.isEmpty, suffix.allSatisfy({ $0.isASCII && $0.isNumber }) { return "claude-\(suffix)" }
            }
        }
        return nil
    }

    static func shellQuote(_ value: String) -> String {
        // A terminal can translate a typed CR to LF; construct it inside the shell instead.
        "'" + value.replacingOccurrences(of: "'", with: "'\\''")
            .replacingOccurrences(of: "\r", with: "'\"$(printf '\\r')\"'") + "'"
    }

    public var terminalCommand: String {
        // tmux passes its command to another shell, so quote that command as well as its path.
        "tmux new-session " + Self.shellQuote(Self.shellQuote(cli.path))
    }

    public var loginCommand: String {
        let overrides = ["CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
                         "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"]
        // A login shell can export another account's credentials; the wrapper selects this one.
        return (["/usr/bin/env"] + overrides.flatMap { ["-u", $0] } + [cli.path, "auth", "login", "--claudeai"])
            .map(Self.shellQuote).joined(separator: " ")
    }

    public static func terminalScript(command: String) -> String {
        let text = command.replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
            .replacingOccurrences(of: "\n", with: "\\n")
            .replacingOccurrences(of: "\r", with: "\\r")
        return """
            tell application "iTerm"
                activate
                set w to (create window with default profile)
                tell current session of w to write text "\(text)"
            end tell
            """
    }

    public static func resolve(names: [String], home: URL = FileManager.default.homeDirectoryForCurrentUser) -> Launch? {
        let candidates = names.compactMap { wrapper([$0]) }.map { home.appending(path: ".local/bin/\($0)") }
        guard let cli = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0.path) }) else { return nil }
        let name = cli.lastPathComponent
        // Each bundle's launcher ends `exec "…/.local/bin/code-N" "$@"`.
        let code = "/.local/bin/code-\(name.dropFirst("claude-".count))\""
        let apps = home.appending(path: "Applications")
        let bundles = (try? FileManager.default.contentsOfDirectory(at: apps, includingPropertiesForKeys: nil)) ?? []
        let editor = bundles.filter { $0.pathExtension == "app" }.sorted { $0.path < $1.path }.first { app in
            let launcher = app.appending(path: "Contents/MacOS/glm-launcher")
            return (try? String(contentsOf: launcher, encoding: .utf8))?.contains(code) == true
        }
        return Launch(cli: cli, editor: editor)
    }
}
