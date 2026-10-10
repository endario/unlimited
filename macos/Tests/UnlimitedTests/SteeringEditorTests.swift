import Foundation
import Testing
@testable import Unlimited
import UnlimitedKit

private let editorNow = Date(timeIntervalSince1970: 1_790_985_600)

private func editorGroups(_ json: String) throws -> [Runner.Incentive] {
    try Runner.decodeIncentives(Data(json.utf8))
}

@Test func accountDraftDoesNotCopyInheritedOrAnotherAccountsSettings() throws {
    let groups = try editorGroups(#"[{"target":"openai","account":null,"multiplier":5,"activated_at":"2026-10-03T00:00:00Z","until":"2026-10-04T00:00:00Z"},{"target":"openai","account":"other","multiplier":0.1,"activated_at":"2026-10-03T00:00:00Z","until":"2026-10-04T00:00:00Z"},{"target":"openai/gpt-6","account":"owner","multiplier":10,"activated_at":"2026-10-03T00:00:00Z","until":"2026-10-04T00:00:00Z"}]"#)
    let draft = SteeringDraft(target: "openai", account: "owner", groups: groups, now: editorNow)
    #expect(draft.multiplier == "off")
    #expect(draft.untilReset)
    #expect(draft.duration == "12h")
    #expect(SteeringDraft.group(target: "openai", account: "owner", groups: groups, now: editorNow) == nil)
}

@Test func accountDraftPreservesCustomFactorAndResetBindingMode() throws {
    let groups = try editorGroups(#"[{"target":"openai","account":"owner","multiplier":1.2345678901234567,"activated_at":"2026-10-03T00:00:00Z","until":null,"bindings":[{"vendor":"openai","account":"owner","names":["account1"],"until":"2026-10-04T00:00:00Z"}]}]"#)
    let draft = SteeringDraft(target: "openai", account: "owner", groups: groups, now: editorNow)
    #expect(draft.multiplier == "1.2345678901234567x")
    #expect(draft.untilReset)
    #expect(draft.duration == "12h")
}

@Test func timedAccountDraftRoundsRemainingDurationUpToAMinute() throws {
    let groups = try editorGroups(#"[{"target":"openai","account":"owner","multiplier":0.1,"activated_at":"2026-10-03T00:00:00Z","until":"2026-10-03T00:01:01Z"}]"#)
    let draft = SteeringDraft(target: "openai", account: "owner", groups: groups, now: editorNow)
    #expect(draft.multiplier == "0.1x")
    #expect(!draft.untilReset)
    #expect(draft.duration == "2m")
}

@Test func expiredAccountGroupsDoNotSeedAnOverride() throws {
    let groups = try editorGroups(#"[{"target":"openai","account":"owner","multiplier":5,"activated_at":"2026-10-02T00:00:00Z","until":"2026-10-03T00:00:00Z"},{"target":"openai","account":"reset","multiplier":10,"activated_at":"2026-10-02T00:00:00Z","until":null,"bindings":[{"vendor":"openai","account":"reset","names":[],"until":"2026-10-03T00:00:00Z"}]}]"#)
    for account in ["owner", "reset"] {
        let draft = SteeringDraft(target: "openai", account: account, groups: groups, now: editorNow)
        #expect(draft.multiplier == "off")
        #expect(draft.untilReset)
        #expect(SteeringDraft.group(target: "openai", account: account, groups: groups, now: editorNow) == nil)
    }
}

@Test func aliasSelectedResetGroupKeepsItsMultiplierInTheScopedEditor() throws {
    let groups = try editorGroups(#"[{"target":"openai","account":"work","multiplier":5,"activated_at":"2026-10-03T00:00:00Z","until":null,"bindings":[{"vendor":"openai","account":"canonical","names":["work"],"until":"2026-10-04T00:00:00Z"}]}]"#)
    let draft = SteeringDraft(target: "openai", account: "work", groups: groups, now: editorNow)
    #expect(draft.multiplier == "5x")
    #expect(draft.untilReset)
    #expect(SteeringDraft.group(target: "openai", account: "canonical", groups: groups, now: editorNow) == nil)
}

@Test func longTimedPoliciesUseADurationTheCLIParserAccepts() throws {
    let expiry = ISO8601DateFormatter().string(from: editorNow.addingTimeInterval(1000 * 86_400))
    let groups = try editorGroups("""
    [{"target":"openai","account":"owner","multiplier":5,
      "activated_at":"2026-10-03T00:00:00Z","until":"\(expiry)"}]
    """)
    let draft = SteeringDraft(target: "openai", account: "owner", groups: groups, now: editorNow)
    #expect(draft.duration == "24000h")
    #expect(!draft.untilReset)
}

@Test func aNeutralOverrideRemainsDistinctFromNoStoredGroup() throws {
    let groups = try editorGroups(#"[{"target":"openai","account":"owner","multiplier":1,"activated_at":"2026-10-03T00:00:00Z","until":"2026-10-04T00:00:00Z"}]"#)
    #expect(SteeringDraft.group(target: "openai", account: "owner", groups: groups, now: editorNow)?.multiplier == 1)
    #expect(SteeringDraft.group(target: "openai", account: "new", groups: groups, now: editorNow) == nil)
    let fresh = SteeringDraft(target: "openai", account: "new", groups: groups, now: editorNow)
    #expect(fresh.multiplier == "off")
    #expect(SteeringDraft(target: "openai", account: "owner", groups: groups, now: editorNow).multiplier == "off")
    #expect(fresh.untilReset)
    #expect(fresh.duration == "12h")
}
