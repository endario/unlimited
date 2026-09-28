import Foundation
import Testing
@testable import UnlimitedKit

/// 2026-09-24T06:00:00Z: ten minutes after most fixture readings, forty after `opencode`'s.
let now = Date(timeIntervalSince1970: 1_790_229_600)

func fixture() throws -> [Reading] {
    let url = try #require(Bundle.module.url(forResource: "readings", withExtension: "json", subdirectory: "Fixtures"))
    return try Reading.decode(Data(contentsOf: url))
}

func strip() throws -> [String: Tile] {
    Dictionary(uniqueKeysWithValues: try Tile.strip(fixture(), now: now).map { ($0.label, $0) })
}

@Test func decodesPythonTimestampsWithMicrosecondsAndOffsets() throws {
    let r = try fixture()[0]
    let t = try #require(r.takenAt).timeIntervalSince1970
    #expect(abs(t - 1_790_229_206.402753) < 1e-6)
    #expect(r.names == ["account2"])
}

@Test func theTileShowsTheWeeklyWindowByRoleNotTheFirstSevenDayWindow() throws {
    // Opus's weekly comes first in this reading and is also 10080 minutes; it must not win.
    #expect(try strip()["CL2"]?.value == .percent(63))
}

@Test func labelsComeFromTheIdentityNamesAndAVendorCode() throws {
    #expect(try Tile.strip(fixture(), now: now).map(\.label) == ["CL1", "CL2", "CDX", "ZAI", "ZAI2", "OPC", "OPC2"])
}

@Test func eachStateSaysWhatItKnows() throws {
    let tiles = try strip()
    #expect(tiles["ZAI2"]?.value == .unread)        // refused: the vendor would not say
    #expect(tiles["ZAI"]?.value == .noWindow)       // Z.ai answered, with no weekly window
    #expect(tiles["CDX"]?.value == .unknown)        // a weekly window whose figure is not known (just reset)
    #expect(tiles["OPC2"]?.dimmed == true)          // throttled: last good reading stands
    #expect(tiles["OPC2"]?.value == .percent(0))
    #expect(tiles["OPC"]?.value == .stale)          // 40 minutes old, past STALE_AFTER
    #expect(tiles["CL1"]?.dimmed == false)
}

@Test func withNoAccountsTheStripIsOneWaitingTile() {
    #expect(Tile.strip([], now: now) == [.waiting])
}

@Test func accountsWithoutNamesAreNumberedApartNotLabelledTheSame() throws {
    let two = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "openai", "account": "b", "status": "ok", "limits": []},
     {"schema": 1, "vendor": "openai", "account": "a", "status": "ok", "limits": []}]
    """.utf8))
    #expect(Tile.strip(two, now: now).map { "\($0.label) \($0.id)" } == ["CDX1 openai/a", "CDX2 openai/b"])
}

@Test func aReadingInAnotherSchemaIsRefusedNotMisread() {
    #expect(throws: Reading.SchemaError.self) {
        try Reading.decode(Data(#"[{"schema": 2, "vendor": "openai", "status": "ok"}]"#.utf8))
    }
}

@Test func aTileCarriesHowMuchOfItsWeekHasPassed() throws {
    let cl2 = try #require(try strip()["CL2"])
    let weekly = try #require(try fixture()[0].primary)
    #expect(cl2.elapsed == weekly.elapsed(now: now), "the weekly window's, not another's")
    #expect(cl2.labelled("CL9").elapsed == cl2.elapsed, "kept when accounts are numbered apart")
}

@Test func withNoWeeklyWindowTheShareOfCreditSpentIsTheFigure() throws {
    let payg = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "neuralwatt", "account": "a", "status": "ok", "limits": [],
      "taken_at": "2026-09-24T05:59:00+00:00",
      "credits": {"enabled": true, "used": 3.62, "limit": 21.0, "currency": "USD"}},
     {"schema": 1, "vendor": "neuralwatt", "account": "b", "status": "ok", "limits": [],
      "taken_at": "2026-09-24T05:59:00+00:00",
      "credits": {"enabled": true, "used": 3.62, "limit": null, "currency": "USD"}}]
    """.utf8))
    #expect(Tile.strip(payg, now: now).map(\.value) == [.percent(17), .noWindow])
}

@Test func offVendorsAreMarkedAndNeverThePick() throws {
    let weekly = """
    {"name": "seven_day", "window_minutes": 10080, "used_at_least": 0.2,
     "resets_at": "2026-09-26T13:00:00+00:00", "held": null, "held_why": null, "role": "weekly"}
    """
    let readings = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "kimi", "account": "k", "status": "ok", "taken_at": "2026-09-24T05:55:00+00:00",
      "limits": [\(weekly)]},
     {"schema": 1, "vendor": "openai", "account": "o", "status": "ok", "taken_at": "2026-09-24T05:55:00+00:00",
      "limits": [\(weekly)]}]
    """.utf8))
    let tiles = Tile.strip(readings, now: now, off: ["kimi"])
    #expect(tiles.first { $0.vendor == "kimi" }?.off == true)
    #expect(tiles.first { $0.vendor == "openai" }?.off == false)
    #expect(Tile.pick(tiles) == tiles.first { $0.vendor == "openai" }?.id,
            "the off vendor's account is never the one to use next")
    #expect(tiles.first { $0.vendor == "kimi" }?.labelled("KMI2").off == true, "kept through relabelling")
    #expect(Tile.strip(readings, now: now).allSatisfy { !$0.off }, "no off set: nothing marked")
}

@Test func anAccountSwitchGraysOnlyThatAccount() throws {
    let weekly = """
    {"name": "seven_day", "window_minutes": 10080, "used_at_least": 0.2,
     "resets_at": "2026-09-26T13:00:00+00:00", "held": null, "held_why": null, "role": "weekly"}
    """
    let readings = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "zai", "account": "4ff9f720", "names": ["claude-glm-2"],
      "status": "ok", "taken_at": "2026-09-24T05:55:00+00:00", "limits": [\(weekly)]},
     {"schema": 1, "vendor": "zai", "account": "da68cb2c", "names": ["claude-glm"],
      "status": "ok", "taken_at": "2026-09-24T05:55:00+00:00", "limits": [\(weekly)]}]
    """.utf8))
    let tiles = Tile.strip(readings, now: now, off: ["zai/claude-glm-2"])
    #expect(tiles.first { $0.id == "zai/4ff9f720" }?.off == true)
    #expect(tiles.first { $0.id == "zai/da68cb2c" }?.off == false, "the sibling account stays offerable")
}
