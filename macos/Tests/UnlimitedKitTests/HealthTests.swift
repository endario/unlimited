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
    #expect(week(used: 0.6, range: [1.0, 1.6]).health(now: now) == .red)
    #expect(week(used: 0.6, range: [0.9, 1.5]).health(now: now) == .red, "150% itself is already red")
}

@Test func mightRunOutIsAmber() {
    #expect(week(used: 0.6, range: [0.9, 1.1]).health(now: now) == .amber)
    #expect(week(used: 0.6, range: [0.9, 1.0]).health(now: now) == .normal, "under 105% is not yet a warning")
    #expect(week(used: 0.6, range: [0.9, 1.05]).health(now: now) == .amber, "105% itself is already a warning")
    #expect(week(used: 0.6, range: [0.9, 1.49]).health(now: now) == .amber, "under 150% stays amber")
}

@Test func roomLeftAtResetIsGreen() {
    #expect(week(used: 0.1, range: [0.2, 0.79]).health(now: now) == .underUsed)
    #expect(week(used: 0.3, range: [0.5, 0.8]).health(now: now) == .normal, "0.8 itself is not under-used")
}

@Test func nearResetWithRoomTheRecentPaceWillNotSpendIsBlue() {
    // 6.1 of 7 days gone: the last 15%.
    #expect(week(used: 0.5, range: [0.6, 0.89], elapsed: 6.1).health(now: now) == .sprint)
    #expect(week(used: 0.1, range: [0.2, 0.3], elapsed: 6.1).health(now: now) == .sprint, "blue beats green")
    #expect(week(used: 0.7, range: [0.85, 0.9], elapsed: 6.1).health(now: now) == .normal, "on course for the cap")
    #expect(week(used: 0.5, range: [0.6, 0.89], elapsed: 5.5).health(now: now) == .normal, "not yet the final stretch")
}

@Test func anUntrustedProjectionSaysNothingEitherWay() {
    // 0.5 days into a week is under 10%: early paces are guesses.
    #expect(week(used: 0.01, range: [0.02, 0.1], elapsed: 0.5).health(now: now) == .normal)
    #expect(week(used: 0.01, range: [0.02, 0.1], elapsed: 0.5, past: 3).health(now: now) == .underUsed)
    #expect(week(used: 0.05, range: [1.1, 1.4], elapsed: 0.5).health(now: now) == .normal)
    #expect(week(used: 1.0, range: [1.1, 1.4], elapsed: 0.5).health(now: now) == .red, "a used-up window is a fact")
    // Early in the window, but past 10% used: enough use to read a pace from.
    #expect(week(used: 0.11, range: [1.1, 1.4], elapsed: 0.5).health(now: now) == .amber)
    #expect(week(used: 0.1, range: [1.1, 1.4], elapsed: 0.5).health(now: now) == .normal, "10% itself is not past it")
    #expect(week(used: 0.2, range: nil).health(now: now) == .normal)
}

@Test func theOverrideIsTheWorstOtherWindowAndOnlyIfWorseThanWeekly() {
    let soon = now.addingTimeInterval(3600), later = now.addingTimeInterval(7200)
    let limits = [week(used: 0.4, range: [0.5, 0.6]),
                  week(used: 0.5, range: [0.9, 1.1], role: "session", minutes: 300, exhausts: later),
                  week(used: 0.6, range: [0.95, 1.2], role: "weekly_model", exhausts: soon),
                  week(used: 0.1, range: [0.1, 0.2], elapsed: 4, role: "month", minutes: 43200)]
    let o = Tile.override(limits, primary: limits[0], now: now)
    #expect(o?.role == "weekly_model", "equal state: the one that runs out first")
    #expect(Tile.override([limits[0], limits[3]], primary: limits[0], now: now) == nil, "nothing worse than weekly")
    #expect(Tile.override(limits.map { $0 }, primary: week(used: 1, range: nil), now: now) == nil, "weekly is already red")
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
    #expect(t.health == .underUsed)
    #expect(t.alternate == Tile.Alternate(role: "session", value: .percent(90), health: .amber))
}

func account(_ id: String, weekly: [Double], elapsed: Double = 3, session: [Double]? = nil, past: Int = 5) -> Reading {
    let resets = { (m: Int, e: Double) in iso(now.addingTimeInterval(Double(m) * 60 - e * 86400)) }
    let s = session.map { r in
        #", {"name": "five_hour", "window_minutes": 300, "used_at_least": 0.1, "resets_at": "\#(resets(300, 0.1))", "role": "session", "projection": {"at_reset": [\#(r[0]), \#(r[1])], "past_windows": 5}}"#
    } ?? ""
    let json = #"{"schema": 1, "vendor": "zai", "account": "\#(id)", "status": "ok", "taken_at": "\#(iso(now))", "names": ["claude-glm-\#(id)"], "limits": [{"name": "seven_day", "window_minutes": 10080, "used_at_least": 0.1, "resets_at": "\#(resets(10080, elapsed))", "role": "weekly", "projection": {"at_reset": [\#(weekly[0]), \#(weekly[1])], "past_windows": \#(past)}}\#(s)]}"#
    return try! Reading.decode(Data("[\(json)]".utf8))[0]
}

func iso(_ d: Date) -> String { ISO8601DateFormatter().string(from: d) }

@Test func theBestPickIsTheAccountWithTheMostRoomAtReset() {
    let tiles = Tile.strip([account("1", weekly: [0.5, 0.6]), account("2", weekly: [0.2, 0.3]),
                            account("3", weekly: [0.9, 1.1])], now: now)
    #expect(tiles.filter(\.best).map(\.label) == ["ZAI2"])
}

@Test func roomAboutToExpireIsPickedBeforeRoomThatWillKeep() {
    let tiles = Tile.strip([account("1", weekly: [0.2, 0.3]), account("2", weekly: [0.6, 0.7], elapsed: 6.2)], now: now)
    #expect(tiles.filter(\.best).map(\.label) == ["ZAI2"])
}

@Test func anAccountStoppedByAnotherWindowIsNotPicked() {
    let tiles = Tile.strip([account("1", weekly: [0.1, 0.2], session: [1.1, 1.3]), account("2", weekly: [0.5, 0.6])], now: now)
    #expect(tiles.filter(\.best).map(\.label) == ["ZAI2"])
}

@Test func aVendorWithOneAccountHasNoPick() {
    #expect(Tile.strip([account("1", weekly: [0.1, 0.2])], now: now).allSatisfy { !$0.best })
}

@Test func anAccountEarlyInItsWeekIsJudgedByWhatItHasUsedNotByAnUntrustedForecast() {
    // Half a day in: its forecast of 103% is a guess, and it has the most room.
    let early = account("1", weekly: [0.6, 1.03], elapsed: 0.5, past: 0)
    let tiles = Tile.strip([early, account("2", weekly: [0.85, 0.94])], now: now)
    #expect(tiles.filter(\.best).map(\.label) == ["ZAI1"])
}

@Test func aPlanWithAMonthlyBucketFollowsItAndShowsAWorseWeekAsTheOverride() throws {
    let data = Data("""
    [{"schema": 1, "vendor": "opencode", "account": "o", "status": "ok", "taken_at": "2026-09-24T05:59:00+00:00",
      "names": ["opencode"], "limits": [
       {"name": "seven_day", "window_minutes": 10080, "used_at_least": 0.8, "resets_at": "2026-09-28T09:28:00+00:00",
        "role": "weekly", "projection": {"at_reset": [1.3, 1.6], "past_windows": 5}},
       {"name": "month", "window_minutes": 43200, "used_at_least": 0.41, "resets_at": "2026-10-19T05:34:00+00:00",
        "role": "month", "projection": {"at_reset": [0.5, 0.6], "past_windows": 5}}]}]
    """.utf8)
    let r = try #require(Reading.decode(data).first)
    #expect(r.primary?.role == "month")
    let t = try #require(Tile.strip([r], now: now).first)
    #expect(t.value == .percent(41))
    #expect(t.alternate == Tile.Alternate(role: "weekly", value: .percent(80), health: .red))
}

@Test func aPlanWithAMonthlyBucketAndACalmWeekShowsNoOverride() throws {
    let data = Data("""
    [{"schema": 1, "vendor": "opencode", "account": "o", "status": "ok", "taken_at": "2026-09-24T05:59:00+00:00",
      "names": ["opencode"], "limits": [
       {"name": "seven_day", "window_minutes": 10080, "used_at_least": 0.1, "resets_at": "2026-09-28T09:28:00+00:00",
        "role": "weekly", "projection": {"at_reset": [0.2, 0.3], "past_windows": 5}},
       {"name": "month", "window_minutes": 43200, "used_at_least": 0.41, "resets_at": "2026-10-19T05:34:00+00:00",
        "role": "month", "projection": {"at_reset": [0.9, 1.0], "past_windows": 5}}]}]
    """.utf8)
    let t = try #require(Tile.strip(Reading.decode(data), now: now).first)
    #expect(t.value == .percent(41))
    #expect(t.alternate == nil)
}
