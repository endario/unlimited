import Foundation

/// Where to open a Claude Code session as one account: the owner's per-account wrappers in
/// `~/.local/bin` (`claude-3`, `claude-glm`, …) and the rebranded VS Code bundle whose launcher
/// runs the matching `code-…` wrapper (see ~/endario/vscode-rebranding).
public struct Launch: Equatable, Sendable {
    /// The CLI wrapper, e.g. `~/.local/bin/claude-3`.
    public let cli: URL
    /// The VS Code bundle for this account, when one is installed.
    public let editor: URL?

    /// `account3` is the `a3` family (`claude-3`); a `claude-…` name is its wrapper already.
    static func wrapper(_ names: [String]) -> String? {
        for n in names {
            if n.hasPrefix("claude-") { return n }
            if n.hasPrefix("account"), let i = Int(n.dropFirst("account".count)) { return "claude-\(i)" }
        }
        return nil
    }

    public static func resolve(names: [String], home: URL = FileManager.default.homeDirectoryForCurrentUser) -> Launch? {
        guard let name = wrapper(names) else { return nil }
        let cli = home.appending(path: ".local/bin/\(name)")
        guard FileManager.default.isExecutableFile(atPath: cli.path) else { return nil }
        // Each bundle's launcher ends `exec "…/.local/bin/code-3" "$@"`.
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
