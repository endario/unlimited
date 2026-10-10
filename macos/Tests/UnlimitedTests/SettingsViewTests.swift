import Foundation
import Testing
@testable import Unlimited
import UnlimitedKit

@MainActor
@Test func globalRulesAreSeparateFromAccountRowsAndOtherPolicies() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let now = Date(timeIntervalSince1970: 1_790_985_600)
    try cli.succeed(takenAt: now, used: 0.42)
    try cli.publishIncentives(#"[{"target":"meta","multiplier":5,"activated_at":"2026-10-03T00:00:00Z","until":"2030-01-01T00:00:00Z"},{"target":"openai","account":"fixture","multiplier":2,"activated_at":"2026-10-03T00:00:00Z","until":"2030-01-01T00:00:00Z"},{"target":"openai","account":"other","multiplier":0.5,"activated_at":"2026-10-03T00:00:00Z","until":"2030-01-01T00:00:00Z"}]"#)
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { now })
    model.refresh()
    try await waitUntil { model.canSteer }
    let view = SettingsView(model: model)
    #expect(view.globalIncentives.map(\.target) == ["meta"])
    #expect(view.globalIncentives.allSatisfy { $0.account == nil })
    #expect(view.otherIncentives.map(\.account) == ["other"])
    #expect(model.incentives.count == 3)
}
