import Foundation
import Testing
@testable import Unlimited
import UnlimitedKit

@MainActor
@Test func failedRefreshRedrawsCachedUsageAsStaleAndThenRecovers() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let takenAt = Date(timeIntervalSince1970: 1_790_985_600)
    var now = takenAt.addingTimeInterval(Tile.staleAfter - 1)
    try cli.succeed(takenAt: takenAt, used: 0.42)
    let model = StripModel(runner: Runner(binary: cli.binary), now: { now })
    model.refresh()
    try await waitUntil { model.canSteer }
    #expect(model.tiles.first?.value == .percent(42))
    #expect(model.problem == nil)

    now = takenAt.addingTimeInterval(Tile.staleAfter + 1)
    try cli.fail()
    model.refresh()
    try await waitUntil { !model.canSteer }
    #expect(model.readings["openai/fixture"]?.takenAt == takenAt)
    #expect(model.readings["openai/fixture"]?.primary?.usedAtLeast == 0.42)
    #expect(model.tiles.first?.id == "openai/fixture")
    #expect(model.tiles.first?.value == .stale)
    #expect(model.tiles.first?.dimmed == true)
    #expect(model.tiles.first?.health == .normal)
    #expect(model.accounts.first?.value == .stale)
    #expect(model.problem != nil)

    let recoveredAt = now
    try cli.succeed(takenAt: recoveredAt, used: 0.71)
    model.refresh()
    try await waitUntil { model.canSteer }
    #expect(model.problem == nil)
    #expect(model.readings["openai/fixture"]?.takenAt == recoveredAt)
    #expect(model.tiles.first?.value == .percent(71))
    #expect(model.tiles.first?.dimmed == false)
}

@MainActor
@Test func failedRefreshSurfacesAnErrorWhileCachedUsageIsStillFresh() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let takenAt = Date(timeIntervalSince1970: 1_790_985_600)
    try cli.succeed(takenAt: takenAt, used: 0.42)
    let model = StripModel(runner: Runner(binary: cli.binary), now: { takenAt })
    model.refresh()
    try await waitUntil { model.canSteer }

    try cli.fail()
    model.refresh()
    try await waitUntil { !model.canSteer }
    #expect(model.readings["openai/fixture"]?.takenAt == takenAt)
    #expect(model.tiles.first?.value == .percent(42))
    #expect(model.tiles.first?.dimmed == false)
    #expect(model.problem != nil)
}

@MainActor
@Test func failedInitialRefreshUsesTheExistingUnreadTile() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    try cli.fail()
    let model = StripModel(runner: Runner(binary: cli.binary))
    model.refresh()
    try await waitUntil { model.problem != nil }
    #expect(model.tiles == [.broken])
    #expect(model.readings.isEmpty)
}

@MainActor
private func waitUntil(_ condition: () -> Bool) async throws {
    let deadline = Date().addingTimeInterval(5)
    while !condition(), Date() < deadline {
        try await Task.sleep(for: .milliseconds(10))
    }
    try #require(condition(), "the CLI refresh did not finish")
}

private struct UsageCLI {
    let directory: URL
    var binary: URL { directory.appending(path: "unlimited") }
    private var failure: URL { directory.appending(path: "failure") }
    private var readings: URL { directory.appending(path: "readings.json") }

    init() throws {
        directory = FileManager.default.temporaryDirectory.appending(path: "unlimited-163-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try """
        #!/bin/sh
        case "$1" in
          --version) printf 'unlimited 0.1.9\\n'; exit 0 ;;
        esac
        if [ -f '\(failure.path)' ]; then
          printf 'fixture CLI failure\\n' >&2
          exit 1
        fi
        case "$1" in
          read) /bin/cat '\(readings.path)' ;;
          off|incentive) printf '[]\\n' ;;
          *) exit 2 ;;
        esac
        """.write(to: binary, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: binary.path)
    }

    func succeed(takenAt: Date, used: Double) throws {
        try? FileManager.default.removeItem(at: failure)
        let timestamp = ISO8601DateFormatter().string(from: takenAt)
        try """
        [{"schema":1,"vendor":"openai","account":"fixture","status":"ok","taken_at":"\(timestamp)",
          "limits":[{"name":"weekly","role":"weekly","window_minutes":10080,"used_at_least":\(used),
          "resets_at":"2026-10-10T00:00:00Z"}]}]
        """.write(to: readings, atomically: true, encoding: .utf8)
    }

    func fail() throws {
        try Data().write(to: failure)
    }

    func remove() {
        try? FileManager.default.removeItem(at: directory)
    }
}
