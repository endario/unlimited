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

@Test func anAccountWithoutAWrapperOffersNoLaunch() throws {
    try withHome(wrappers: ["claude-3"], launchers: [:]) { h in
        #expect(Launch.resolve(names: [], home: h) == nil, "Codex and Grok carry no name")
        #expect(Launch.resolve(names: ["account2"], home: h) == nil, "claude-2 is not installed")
        #expect(Launch.resolve(names: ["account3"], home: h)?.editor == nil, "the CLI alone still launches")
    }
}
