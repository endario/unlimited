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
    #expect(tiles["ZAI"]?.value == .noWeekly)       // Z.ai answered, with no weekly window
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
    let weekly = try #require(try fixture()[0].weekly)
    #expect(cl2.elapsed == weekly.elapsed(now: now), "the weekly window's, not another's")
    #expect(cl2.labelled("CL9").elapsed == cl2.elapsed, "kept when accounts are numbered apart")
}
