import Foundation
import Testing
@testable import UnlimitedKit

/// A weekly window opened `elapsed` days before `now`, with the projection's range at reset.
func week(used: Double?, range: [Double]?, elapsed: Double = 3, past: Int = 0, held: Bool? = nil,
          role: String = "weekly", minutes: Int = 10080, exhausts: Date? = nil) -> Limit {
    let resets = now.addingTimeInterval(Double(minutes) * 60 - elapsed * 86400)
    return Limit(name: role, windowMinutes: minutes, usedAtLeast: used, resetsAt: resets, held: held, role: role,
                 scope: nil, projection: range.map { Projection(atReset: $0, exhaustsAt: exhausts, pastWindows: past) })
}

@Test func heldOrUsedUpOrSureToRunOutIsRed() {
    #expect(week(used: 0.2, range: [0.3, 0.4], held: true).health(now: now) == .red)
    #expect(week(used: 1.0, range: nil).health(now: now) == .red)
    #expect(week(used: 0.6, range: [1.0, 1.3]).health(now: now) == .red)
}

@Test func mightRunOutIsAmber() {
    #expect(week(used: 0.6, range: [0.9, 1.0]).health(now: now) == .amber)
}

@Test func roomLeftAtResetSaysUseMore() {
    #expect(week(used: 0.1, range: [0.2, 0.69]).health(now: now) == .useMore)
    #expect(week(used: 0.3, range: [0.5, 0.7]).health(now: now) == .normal, "0.7 itself is not room to spare")
}

@Test func anUntrustedProjectionSaysNothingEitherWay() {
    // 0.5 days into a week is under 10%: early paces are guesses.
    #expect(week(used: 0.01, range: [0.02, 0.1], elapsed: 0.5).health(now: now) == .normal)
    #expect(week(used: 0.01, range: [0.02, 0.1], elapsed: 0.5, past: 3).health(now: now) == .useMore)
    #expect(week(used: 0.5, range: [1.1, 1.4], elapsed: 0.5).health(now: now) == .normal)
    #expect(week(used: 1.0, range: [1.1, 1.4], elapsed: 0.5).health(now: now) == .red, "a used-up window is a fact")
    #expect(week(used: 0.2, range: nil).health(now: now) == .normal)
}

@Test func theOverrideIsTheWorstOtherWindowAndOnlyIfWorseThanWeekly() {
    let soon = now.addingTimeInterval(3600), later = now.addingTimeInterval(7200)
    let limits = [week(used: 0.4, range: [0.5, 0.6]),
                  week(used: 0.5, range: [0.9, 1.1], role: "session", minutes: 300, exhausts: later),
                  week(used: 0.6, range: [0.95, 1.2], role: "weekly_model", exhausts: soon),
                  week(used: 0.1, range: [0.1, 0.2], role: "month", minutes: 43200)]
    let o = Tile.override(limits, weekly: limits[0], now: now)
    #expect(o?.role == "weekly_model", "equal state: the one that runs out first")
    #expect(Tile.override([limits[0], limits[3]], weekly: limits[0], now: now) == nil, "nothing worse than weekly")
    #expect(Tile.override(limits.map { $0 }, weekly: week(used: 1, range: nil), now: now) == nil, "weekly is already red")
}

@Test func aTileCarriesItsHealthAndItsOverride() throws {
    let data = Data("""
    [{"schema": 1, "vendor": "kimi", "account": "k", "status": "ok", "taken_at": "2026-09-24T05:59:00+00:00",
      "names": ["claude-kimi"], "limits": [
       {"name": "five_hour", "window_minutes": 300, "used_at_least": 1.0, "resets_at": "2026-09-24T06:09:00+00:00",
        "role": "session"},
       {"name": "seven_day", "window_minutes": 10080, "used_at_least": 0.81, "resets_at": "2026-09-28T09:28:00+00:00",
        "role": "weekly", "projection": {"at_reset": [2.0, 3.3], "exhausts_at": "2026-09-24T12:48:00+00:00",
                                         "past_windows": 0}}]}]
    """.utf8)
    let t = try #require(Tile.strip(Reading.decode(data), now: now).first)
    #expect(t.value == .percent(81))
    #expect(t.health == .red)
    #expect(t.alternate == nil, "the 5h window is red too, but no worse than weekly")
}

@Test func aTileWhoseSessionIsWorseThanItsWeeklyShowsTheSessionToo() throws {
    let data = Data("""
    [{"schema": 1, "vendor": "zai", "account": "z", "status": "ok", "taken_at": "2026-09-24T05:59:00+00:00",
      "names": ["claude-glm"], "limits": [
       {"name": "seven_day", "window_minutes": 10080, "used_at_least": 0.05, "resets_at": "2026-09-28T09:28:00+00:00",
        "role": "weekly", "projection": {"at_reset": [0.1, 0.2], "past_windows": 5}},
       {"name": "five_hour", "window_minutes": 300, "used_at_least": 0.9, "resets_at": "2026-09-24T07:00:00+00:00",
        "role": "session", "projection": {"at_reset": [0.95, 1.2], "past_windows": 5}}]}]
    """.utf8)
    let t = try #require(Tile.strip(Reading.decode(data), now: now).first)
    #expect(t.health == .useMore)
    #expect(t.alternate == Tile.Alternate(role: "session", value: .percent(90), health: .amber))
}
