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

@Test func unavailableUsageShowsAQuestionMarkInsteadOfAQuotaWarning() throws {
    let tiles = try strip()
    #expect(tiles["ZAI2"]?.value.text == "?", "an unread account has no usage figure")
    #expect(tiles["OPC"]?.value.text == "?", "an old reading cannot describe current usage")
    #expect(tiles["CDX"]?.value.text == "?")
    #expect(tiles["ZAI"]?.value.text == "—", "an answered account with no window stays distinct")
    #expect(tiles["OPC2"]?.value.text == "0", "backoff retains the last known percentage")
    #expect(Tile.waiting.value.text == "…")
}

@Test func aSpentWindowShowsACrossInsteadOfItsPercent() {
    #expect(Tile.Value.percent(100).text == "✘")
    #expect(Tile.Value.percent(142).text == "✘", "over the limit is spent, not a figure")
    #expect(Tile.Value.localPercent(100).text == "✘")
    #expect(Tile.Value.percent(99).text == "99", "a window that still has room shows its figure")
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

@Test func withNoWindowTheRemainingBalanceIsTheFigure() throws {
    let prepaid = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "deepseek", "account": "d", "status": "ok", "limits": [],
      "taken_at": "2026-09-24T05:59:00+00:00",
      "credits": {"enabled": true, "used": null, "limit": null, "balance": 48.09, "currency": "CNY"}}]
    """.utf8))
    let tile = try #require(Tile.strip(prepaid, now: now).first)
    #expect(tile.value == .credit(48.09, currency: "CNY"))
    #expect(tile.value.text == "48", "a balance is a plain figure in the strip, rounded")
    #expect(tile.value.showsData)
    #expect(!tile.value.isMissing)
}

@Test func aRunningPeakRateWindowMarksTheAccount() throws {
    let readings = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "zai", "account": "a", "status": "ok", "taken_at": "2026-09-24T05:59:00+00:00",
      "limits": [{"name": "seven_day", "window_minutes": 10080, "used_at_least": 0.2, "role": "weekly"}],
      "steering": {"rates": [
        {"offerings": null, "from": "2026-09-24T06:00:00+00:00", "to": "2026-09-24T10:00:00+00:00", "factor": 2},
        {"offerings": null, "from": "2026-09-25T06:00:00+00:00", "to": "2026-09-25T10:00:00+00:00", "factor": 2}]}}]
    """.utf8))
    let tile = try #require(Tile.strip(readings, now: now).first)
    #expect(tile.peak)
    #expect(tile.labelled("OWN").peak, "kept when accounts are numbered apart")
    #expect(!Tile.strip(readings, now: now.addingTimeInterval(-60)).first!.peak, "a window that has not opened")
    #expect(!Tile.strip(readings, now: now.addingTimeInterval(4 * 3600)).first!.peak, "a window that has closed")
}

@Test func aDiscountedRateWindowIsNotPeak() throws {
    let readings = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "zai", "account": "a", "status": "ok", "taken_at": "2026-09-24T05:59:00+00:00",
      "limits": [{"name": "seven_day", "window_minutes": 10080, "used_at_least": 0.2, "role": "weekly"}],
      "steering": {"rates": [
        {"offerings": null, "from": "2026-09-24T05:00:00+00:00", "to": "2026-09-24T07:00:00+00:00", "factor": 0.5}]}}]
    """.utf8))
    #expect(Tile.strip(readings, now: now).first?.peak == false)
}

@Test func offVendorsAreMarkedAndNeverThePick() throws {
    func weekly(_ used: Double) -> String {
        """
        {"name": "seven_day", "window_minutes": 10080, "used_at_least": \(used),
         "resets_at": "2026-09-26T13:00:00+00:00", "held": null, "held_why": null, "role": "weekly"}
        """
    }
    // kimi is the account pick would otherwise choose (the lower heading wins the tie-break).
    let readings = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "kimi", "account": "k", "status": "ok", "taken_at": "2026-09-24T05:55:00+00:00",
      "limits": [\(weekly(0.1))]},
     {"schema": 1, "vendor": "openai", "account": "o", "status": "ok", "taken_at": "2026-09-24T05:55:00+00:00",
      "limits": [\(weekly(0.6))]}]
    """.utf8))
    let tiles = Tile.strip(readings, now: now, off: ["kimi"])
    #expect(tiles.first { $0.vendor == "kimi" }?.off == true)
    #expect(tiles.first { $0.vendor == "openai" }?.off == false)
    #expect(Tile.pick(tiles) == tiles.first { $0.vendor == "openai" }?.id,
            "the off vendor's account is never the one to use next")
    let live = Tile.strip(readings, now: now)
    #expect(Tile.pick(live) == live.first { $0.vendor == "kimi" }?.id,
            "without the switch kimi is the pick — the guarded assertion has something to bite on")
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

@Test func aReadingCarriesTheVendorsAccountIdWhereTheCliKnowsIt() throws {
    let readings = try fixture()
    let z1 = try #require(readings.first { $0.account == "z1" })
    #expect(z1.vendorAccount == "17261781696668863")
    #expect(z1.shortVendorAccount == "17261781…")
    // A CLI before account identity, or an account not yet identified, says nothing.
    #expect(try #require(readings.first { $0.account == "z2" }).vendorAccount == nil)
    #expect(try #require(readings.first { $0.account == "z2" }).shortVendorAccount == nil)
}

@Test func loggedSevenDayTokensReplaceTheQuotaFigureWithoutBecomingAQuota() throws {
    let readings = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "woyo", "account": "synthetic", "status": "ok",
      "taken_at": "2026-09-24T05:59:00+00:00", "limits": [],
      "usage": {"source": "gateway-log", "as_of": "2026-09-24T06:00:00+00:00",
        "collected_at": "2026-09-24T05:59:00+00:00", "periods": [
          {"window_minutes": 10080, "starts_at": "2026-09-17T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 2851, "output_tokens": 85, "total_tokens": 2936, "cache_read_tokens": 1088, "cache_creation_tokens": 0, "coverage": "partial", "reasons": ["horizon-edge"]},
          {"window_minutes": 43200, "starts_at": "2026-08-25T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 2851, "output_tokens": 85, "total_tokens": 2936, "cache_read_tokens": 1088, "cache_creation_tokens": 0, "coverage": "partial", "reasons": ["horizon-edge"]}
        ]}}
    ]
    """.utf8))

    let tiles = Tile.strip(readings, now: now)

    #expect(tiles[0].value.text == "2,936")
    #expect(!tiles[0].best)
    #expect(tiles[0].health == .normal)
    #expect(!tiles[0].isRoutine)
}

@Test func measuredUsageDecodesAdditivelyAndRetainsTokensOnARefusedRead() throws {
    let readings = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "woyo", "account": "ok", "status": "ok", "limits": [],
      "usage": {"source": "gateway-log", "as_of": "2026-09-24T06:00:00+00:00", "collected_at": "2026-09-24T05:59:00+00:00", "periods": [
        {"window_minutes": 10080, "starts_at": "2026-09-17T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 2851, "output_tokens": 85, "total_tokens": 2936, "cache_read_tokens": 1088, "cache_creation_tokens": 0, "coverage": "partial", "reasons": ["horizon-edge"]},
        {"window_minutes": 43200, "starts_at": "2026-08-25T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 2851, "output_tokens": 85, "total_tokens": 2936, "cache_read_tokens": 1088, "cache_creation_tokens": 0, "coverage": "partial", "reasons": ["horizon-edge"]}
      ]}},
     {"schema": 1, "vendor": "woyo", "account": "refused", "status": "refused", "limits": [],
      "usage": {"source": "gateway-log", "as_of": "2026-09-24T06:00:00+00:00", "collected_at": "2026-09-24T05:59:00+00:00", "periods": [
        {"window_minutes": 10080, "starts_at": "2026-09-17T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 2851, "output_tokens": 85, "total_tokens": 2936, "cache_read_tokens": 1088, "cache_creation_tokens": 0, "coverage": "partial", "reasons": ["horizon-edge"]},
        {"window_minutes": 43200, "starts_at": "2026-08-25T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 2851, "output_tokens": 85, "total_tokens": 2936, "cache_read_tokens": 1088, "cache_creation_tokens": 0, "coverage": "partial", "reasons": ["horizon-edge"]}
      ]}},
     {"schema": 1, "vendor": "openai", "account": "old", "status": "ok", "limits": []}]
    """.utf8))

    let usage = try #require(readings[0].usage)
    #expect(usage.periods[0].totalTokens == 2936)
    #expect(usage.periods[1].windowMinutes == 43200)
    #expect(usage.periods[0].coverage == "partial")
    #expect(readings[2].usage == nil)

    let refused = try #require(Tile.strip(readings, now: now).first { $0.id == "woyo/refused" })
    #expect(refused.value == .tokens(2936, windowMinutes: 10080))
    #expect(refused.dimmed)
    #expect(refused.health == .normal)
    #expect(!refused.best)
    #expect(!refused.isRoutine)
}

@Test func loggedTokensNeverCompeteInQuotaSelection() {
    let measured = Tile(id: "woyo/logged", label: "WYO", value: .tokens(2_936, windowMinutes: 10_080), dimmed: false)
    let quota = Tile(id: "woyo/quota", label: "WYO2", value: .percent(60), dimmed: false)
    #expect(Tile.pick([measured, quota]) == quota.id)
}

@Test func aSpentAccountIsNeverThePick() {
    // Healthy health on a spent figure: the exclusion is the spent mark, not the colour.
    let spent = Tile(id: "openai/spent", label: "CDX", value: .percent(100), dimmed: false, health: .normal)
    let roomy = Tile(id: "openai/roomy", label: "CDX2", value: .percent(20), dimmed: false, health: .normal)
    #expect(Tile.pick([spent, roomy]) == roomy.id)
}

@Test func loggedTokenTilesUseTheStripLayoutForTheirWiderClickAndPopoverBounds() {
    let percent = Tile(id: "openai/percent", label: "CDX", value: .percent(27), dimmed: false)
    let tokens = Tile(id: "woyo/tokens", label: "WYO", value: .tokens(123_456_789_012_345, windowMinutes: 10080), dimmed: false)
    let layout = StripLayout(tiles: [percent, tokens], spacing: 3, padding: 2)

    #expect(layout.width(of: tokens) > layout.width(of: percent))
    let tokenRect = layout.rect(of: tokens)
    #expect(tokenRect.minX == layout.rect(of: percent).maxX + 3)
    #expect(layout.tile(at: tokenRect.minX + tokenRect.width - 0.1)?.id == tokens.id)
}

@Test func woyoUsesItsNativeNameAndCompactLargeLoggedTokenFigure() throws {
    let reading = try #require(try Reading.decode(Data("""
    [{"schema": 1, "vendor": "woyo", "account": "synthetic", "status": "ok", "limits": [],
      "usage": {"source": "gateway-log", "as_of": "2026-09-24T06:00:00+00:00", "collected_at": "2026-09-24T05:59:00+00:00", "periods": [
        {"window_minutes": 10080, "starts_at": "2026-09-17T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 0, "output_tokens": 0, "total_tokens": 12345678, "cache_read_tokens": 0, "cache_creation_tokens": 0, "coverage": "partial", "reasons": []}
      ]}}]
    """.utf8)).first)
    let tile = try #require(Tile.strip([reading], now: now).first)

    #expect(Tile.vendorName("woyo") == "Woyo")
    #expect(tile.label == "WY")
    #expect(tile.value.text == "12.3M")
}
