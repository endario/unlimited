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
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { now })
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
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { takenAt })
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
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults)
    model.refresh()
    try await waitUntil { model.problem != nil }
    #expect(model.tiles == [.broken])
    #expect(model.readings.isEmpty)
}

@MainActor
@Test func modelLoadsAndSavesPreferencesInItsFixtureSuite() throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let other = try UsageCLI()
    defer { other.remove() }
    var saved = Preferences()
    saved.labels["openai/fixture"] = "OWN"
    saved.order = ["openai/fixture"]
    cli.defaults.set(try JSONEncoder().encode(saved), forKey: "preferences")

    let model = StripModel(defaults: cli.defaults)
    #expect(model.prefs == saved)
    model.arrange { $0.rename("openai/fixture", to: "NEW") }
    let persisted = try #require(cli.defaults.data(forKey: "preferences"))
    #expect(try JSONDecoder().decode(Preferences.self, from: persisted).labels["openai/fixture"] == "NEW")
    #expect(StripModel(defaults: cli.defaults).prefs.labels["openai/fixture"] == "NEW")
    #expect(StripModel(defaults: other.defaults).prefs == Preferences())
    #expect(other.defaults.data(forKey: "preferences") == nil)
}

@MainActor
@Test func hiddenAccountsKeepRefreshingAndCanBeShownAfterRelaunch() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let takenAt = Date(timeIntervalSince1970: 1_790_985_600)
    try cli.succeed(takenAt: takenAt, used: 0.42)
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { takenAt })
    model.refresh()
    try await waitUntil { model.readings["openai/fixture"] != nil }
    let switches = model.offSwitches

    model.arrange { $0.hide("openai/fixture", true) }
    #expect(model.tiles == [.waiting])
    #expect(model.accounts.first?.id == "openai/fixture")
    #expect(model.readings["openai/fixture"]?.primary?.usedAtLeast == 0.42)
    #expect(model.offSwitches == switches)

    try cli.succeed(takenAt: takenAt, used: 0.71)
    model.refresh()
    try await waitUntil { model.readings["openai/fixture"]?.primary?.usedAtLeast == 0.71 }
    #expect(model.tiles == [.waiting])
    #expect(model.accounts.first?.value == .percent(71))
    #expect(model.offSwitches == switches)

    let relaunched = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { takenAt })
    #expect(relaunched.prefs.hidden.contains("openai/fixture"))
    relaunched.refresh()
    try await waitUntil { relaunched.readings["openai/fixture"] != nil }
    #expect(relaunched.tiles == [.waiting])
    relaunched.arrange { $0.hide("openai/fixture", false) }
    #expect(relaunched.tiles.first?.id == "openai/fixture")
    #expect(relaunched.tiles.first?.value == .percent(71))
    #expect(!StripModel(defaults: cli.defaults).prefs.hidden.contains("openai/fixture"))
}

@MainActor
@Test func accountBanWritesItsScopeAndPublishesTheCLIReadback() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let takenAt = Date(timeIntervalSince1970: 1_790_985_600)
    try cli.succeed(takenAt: takenAt, used: 0.42)
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { takenAt })
    model.refresh()
    try await waitUntil { model.canOffer }

    try cli.publishSwitches([Runner.Switch(target: "openai", account: "fixture")])
    model.offer("openai", account: "fixture", true)
    #expect(model.steeringPending)
    try await waitUntil { !model.steeringPending }
    #expect(try cli.arguments() == ["off", "openai", "--account", "fixture"])
    #expect(model.tiles.first?.off == true)
    #expect(model.offerProblem == nil)
    #expect(model.readings["openai/fixture"] != nil)
    #expect(model.prefs.hidden.isEmpty)

    try cli.publishSwitches([])
    model.offer("openai", account: "fixture", false)
    try await waitUntil { !model.steeringPending }
    #expect(try cli.arguments() == ["on", "openai", "--account", "fixture"])
    #expect(model.tiles.first?.off == false)
}

@MainActor
@Test func accountMultiplierTransportsDurationAndKeepsNeutralDifferentFromClear() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let takenAt = Date(timeIntervalSince1970: 1_790_985_600)
    try cli.succeed(takenAt: takenAt, used: 0.42)
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { takenAt })
    model.refresh()
    try await waitUntil { model.canSteer }
    try cli.publishIncentives(#"[{"target":"openai","account":"fixture","multiplier":1,"activated_at":"2026-10-03T00:00:00Z","until":"2026-10-04T00:00:00Z"}]"#)
    model.steer(target: "openai", multiplier: "1x", account: "fixture", duration: "12h")
    try await waitUntil { !model.steeringPending }
    #expect(try cli.arguments() == ["incentive", "openai", "1x", "--account", "fixture", "--for", "12h"])
    #expect(model.incentives.first?.multiplier == 1)
    #expect(model.incentives.first?.account == "fixture")
    #expect(model.steeringProblem == nil)

    try cli.publishIncentives("[]")
    model.steer(target: "openai", multiplier: "off", account: "fixture")
    try await waitUntil { !model.steeringPending }
    #expect(try cli.arguments() == ["incentive", "openai", "off", "--account", "fixture"])
    #expect(model.incentives.isEmpty)
}

@MainActor
@Test func refusedPolicyWritesSurfaceTheirReasonWithoutOptimisticState() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let takenAt = Date(timeIntervalSince1970: 1_790_985_600)
    try cli.succeed(takenAt: takenAt, used: 0.42)
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { takenAt })
    model.refresh()
    try await waitUntil { model.canOffer && model.canSteer }
    try cli.refuseMutation()
    model.offer("openai", account: "fixture", true)
    try await waitUntil { !model.steeringPending }
    #expect(model.offerProblem == "fixture policy write refused")
    #expect(model.offSwitches.isEmpty)
    #expect(model.tiles.first?.off == false)

    model.steer(target: "openai", multiplier: "5x", account: "fixture", duration: "12h")
    try await waitUntil { !model.steeringPending }
    #expect(model.steeringProblem == "fixture policy write refused")
    #expect(model.incentives.isEmpty)
}

@MainActor
@Test func reorderedAccountStripKeepsHiddenReadingsAndPersistsItsVisibleOrder() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let takenAt = Date(timeIntervalSince1970: 1_790_985_600)
    try cli.succeedMany(takenAt: takenAt)
    var prefs = Preferences()
    prefs.order = (1...12).map { "anthropic/fixture-\($0)" }
    prefs.labels = Dictionary(uniqueKeysWithValues: (1...12).map { ("anthropic/fixture-\($0)", "CL\($0)") })
    cli.defaults.set(try JSONEncoder().encode(prefs), forKey: "preferences")
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { takenAt })
    model.refresh()
    try await waitUntil { model.accounts.count == 12 }
    model.arrange {
        $0.hide("anthropic/fixture-3", true)
        $0.hide("anthropic/fixture-10", true)
        $0.step("anthropic/fixture-2", by: 1)
        $0.step("anthropic/fixture-1", by: -1)
        $0.step("anthropic/fixture-12", by: 1)
    }
    let expected = ["anthropic/fixture-1", "anthropic/fixture-4", "anthropic/fixture-2",
                    "anthropic/fixture-5", "anthropic/fixture-6", "anthropic/fixture-7",
                    "anthropic/fixture-8", "anthropic/fixture-9", "anthropic/fixture-11", "anthropic/fixture-12"]
    #expect(model.tiles.map(\.id) == expected)
    #expect(model.readings["anthropic/fixture-3"] != nil)
    #expect(model.readings["anthropic/fixture-10"] != nil)
    #expect(model.accounts.count == 12)
    let relaunched = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { takenAt })
    relaunched.refresh()
    try await waitUntil { relaunched.accounts.count == 12 }
    #expect(relaunched.tiles.map(\.id) == expected)
}

@MainActor
@Test func modelUsesItsFixtureCustomPathForRefreshAndReplacement() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let replacement = try UsageCLI()
    defer { replacement.remove() }
    let takenAt = Date(timeIntervalSince1970: 1_790_985_600)
    try cli.succeed(takenAt: takenAt, used: 0.42)
    try replacement.succeed(takenAt: takenAt, used: 0.71)
    cli.defaults.set(cli.binary.path, forKey: "unlimitedPath")
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { takenAt })
    #expect(model.customPath == cli.binary.path)
    model.refresh()
    try await waitUntil { model.tiles.first?.value == .percent(42) }

    model.customPath = replacement.binary.path
    try await waitUntil { model.tiles.first?.value == .percent(71) }
    #expect(cli.defaults.string(forKey: "unlimitedPath") == replacement.binary.path)
    #expect(model.customPath == replacement.binary.path)
    #expect(StripModel(defaults: replacement.defaults).customPath.isEmpty)
    #expect(replacement.defaults.string(forKey: "unlimitedPath") == nil)
}

@MainActor
private func waitUntil(_ condition: () -> Bool) async throws {
    let deadline = Date().addingTimeInterval(15)
    while !condition(), Date() < deadline {
        try await Task.sleep(for: .milliseconds(10))
    }
    try #require(condition(), "the CLI refresh did not finish")
}

private struct UsageCLI {
    let directory: URL
    let suiteName: String
    let defaults: UserDefaults
    var binary: URL { directory.appending(path: "unlimited") }
    private var failure: URL { directory.appending(path: "failure") }
    private var readings: URL { directory.appending(path: "readings.json") }
    private var switches: URL { directory.appending(path: "switches.json") }
    private var incentives: URL { directory.appending(path: "incentives.json") }
    private var command: URL { directory.appending(path: "command") }
    private var mutationFailure: URL { directory.appending(path: "mutation-failure") }

    init() throws {
        suiteName = "unlimited-tests-\(UUID().uuidString)"
        defaults = try #require(UserDefaults(suiteName: suiteName))
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
          off|on|incentive)
            if [ "$2" = '--json' ]; then
              if [ "$1" = 'off' ]; then /bin/cat '\(switches.path)'
              else /bin/cat '\(incentives.path)'; fi
            else
              printf '%s\\n' "$@" > '\(command.path)'
              if [ -f '\(mutationFailure.path)' ]; then
                printf 'fixture policy write refused\\n' >&2
                exit 2
              fi
            fi ;;
          *) exit 2 ;;
        esac
        """.write(to: binary, atomically: true, encoding: .utf8)
        try "[]".write(to: switches, atomically: true, encoding: .utf8)
        try "[]".write(to: incentives, atomically: true, encoding: .utf8)
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

    func succeedMany(takenAt: Date) throws {
        let timestamp = ISO8601DateFormatter().string(from: takenAt)
        let values: [[String: Any]] = (1...12).map { index in
            ["schema": 1, "vendor": "anthropic", "account": "fixture-\(index)",
             "names": ["account\(index)"], "status": "ok", "taken_at": timestamp,
             "limits": [["name": "weekly", "role": "weekly", "window_minutes": 10080,
                         "used_at_least": 0.42, "resets_at": "2026-10-10T00:00:00Z"]]]
        }
        try JSONSerialization.data(withJSONObject: values).write(to: readings)
    }

    func publishSwitches(_ value: [Runner.Switch]) throws {
        try JSONEncoder().encode(value).write(to: switches)
    }

    func publishIncentives(_ json: String) throws {
        try json.write(to: incentives, atomically: true, encoding: .utf8)
    }

    func refuseMutation() throws {
        try Data().write(to: mutationFailure)
    }

    func arguments() throws -> [String] {
        try String(contentsOf: command, encoding: .utf8).split(separator: "\n").map(String.init)
    }

    func fail() throws {
        try Data().write(to: failure)
    }

    func remove() {
        defaults.removePersistentDomain(forName: suiteName)
        try? FileManager.default.removeItem(at: directory)
    }
}
