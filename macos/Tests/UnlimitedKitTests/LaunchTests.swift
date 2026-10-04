import Foundation
import Testing
@testable import UnlimitedKit

private func withHome<T>(wrappers: [String], launchers: [String: String], _ body: (URL) throws -> T) throws -> T {
    let home = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: home) }
    let bin = home.appending(path: ".local/bin")
    try FileManager.default.createDirectory(at: bin, withIntermediateDirectories: true)
    for w in wrappers {
        let f = bin.appending(path: w)
        try "#!/bin/sh\n".write(to: f, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: f.path)
    }
    for (app, target) in launchers {
        let dir = home.appending(path: "Applications/\(app).app/Contents/MacOS")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try "exec \"\(home.path)/.local/bin/\(target)\" \"$@\"\n"
            .write(to: dir.appending(path: "glm-launcher"), atomically: true, encoding: .utf8)
    }
    return try body(home)
}

@Test func anAccountOpensItsOwnWrapperAndTheBundleThatRunsIt() throws {
    // code-3 must not match code-3x, nor code-glm match code-glm-2: each decoy sorts first.
    try withHome(wrappers: ["claude-3", "claude-glm"],
                 launchers: ["Code A": "code-3", "Code A decoy": "code-3x",
                             "Code B": "code-glm", "Code B decoy": "code-glm-2"]) { h in
        let a3 = try #require(Launch.resolve(names: ["account3"], home: h))
        #expect(a3.cli.lastPathComponent == "claude-3")
        #expect(a3.editor?.lastPathComponent == "Code A.app")
        #expect(Launch.resolve(names: ["claude-glm"], home: h)?.editor?.lastPathComponent == "Code B.app")
    }
}

private func output(_ executable: String, _ arguments: [String], environment: [String: String]? = nil) throws -> String {
    let process = Process(), pipe = Pipe()
    process.executableURL = URL(filePath: executable)
    process.arguments = arguments
    process.environment = environment
    process.standardOutput = pipe
    try process.run()
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    #expect(process.terminationStatus == 0)
    return String(decoding: data, as: UTF8.self)
}

@Test(arguments: [("claude-2", "account2"), ("claude-\r2", "claude-\r2")])
func loginUsesTheSelectedWrapperWithoutInheritedCredentials(wrapper: String, alias: String) throws {
    try withHome(wrappers: [wrapper], launchers: [:]) { home in
        let launch = try #require(Launch.resolve(names: [alias], home: home))
        #expect(!launch.loginCommand.contains("\r"))
        try """
        #!/bin/sh
        printf '%s\\n' "$0" "$@" "${CLAUDE_CONFIG_DIR-unset}" "${CLAUDE_CODE_OAUTH_TOKEN-unset}" \\
            "${CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR-unset}" "${ANTHROPIC_AUTH_TOKEN-unset}" "${ANTHROPIC_API_KEY-unset}"
        """.write(to: launch.cli, atomically: true, encoding: .utf8)
        let inherited = ["CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
                         "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"]
        let env = Dictionary(uniqueKeysWithValues: inherited.map { ($0, "wrong-account") })
        let got = try output("/bin/sh", ["-c", launch.loginCommand], environment: env)
        #expect(got == "\(launch.cli.path)\nauth\nlogin\n--claudeai\nunset\nunset\nunset\nunset\nunset\n")
    }
}

@Test func shellQuotingKeepsMetacharactersInsideOneArgument() throws {
    let argument = "space 'quote' \"double\" \\slash\nnewline\rcarriage $(printf injected); tail"
    let quoted = Launch.shellQuote(argument)
    #expect(!quoted.contains("\r"))
    #expect(try output("/bin/sh", ["-c", "printf %s " + quoted]) == argument)
}

@Test func terminalScriptPreservesTheComposedCommand() throws {
    let launch = Launch(cli: URL(filePath: "/tmp/a 'quote' \"double\" \\slash\nnewline\rcarriage $(printf injected)/claude-2"), editor: nil)
    for command in [launch.loginCommand, launch.terminalCommand] {
        let script = Launch.terminalScript(command: command)
        let prefix = "tell current session of w to write text "
        let line = try #require(script.split(separator: "\n").first { $0.contains(prefix) })
        let literal = try #require(line.range(of: prefix)).upperBound
        let expression = String(line[literal...])
        let transported = try output("/usr/bin/osascript", ["-e", "return " + expression])
        #expect(transported == command + "\n")
        #expect(!transported.contains("\r"))
    }
}

@Test func tmuxReceivesTheQuotedInnerCommand() throws {
    let launch = Launch(cli: URL(filePath: "/tmp/a 'quote' \"double\" \\slash\nnewline\rcarriage $(printf injected)/claude-2"), editor: nil)
    let got = try output("/bin/sh", ["-c", "tmux() { shift; /bin/sh -c \"printf %s $1\"; }; " + launch.terminalCommand])
    #expect(got == launch.cli.path)
}

@Test func aLaterAliasWithAnInstalledWrapperStillOffersLaunch() throws {
    try withHome(wrappers: ["claude-2"], launchers: [:]) { home in
        #expect(Launch.resolve(names: ["account1", "account2"], home: home)?.cli.lastPathComponent == "claude-2")
    }
}

@Test func numericAliasesDoNotSignInToANormalizedSibling() throws {
    try withHome(wrappers: ["claude-2"], launchers: [:]) { home in
        #expect(Launch.resolve(names: ["account02"], home: home) == nil)
    }
    try withHome(wrappers: ["claude-02"], launchers: [:]) { home in
        #expect(Launch.resolve(names: ["account02"], home: home)?.cli.lastPathComponent == "claude-02")
    }
}

@Test func anAccountWithoutAWrapperOffersNoLaunch() throws {
    try withHome(wrappers: ["claude-3"], launchers: [:]) { h in
        #expect(Launch.resolve(names: [], home: h) == nil, "Codex and Grok carry no name")
        #expect(Launch.resolve(names: ["account2"], home: h) == nil, "claude-2 is not installed")
        #expect(Launch.resolve(names: ["account3"], home: h)?.editor == nil, "the CLI alone still launches")
    }
}
