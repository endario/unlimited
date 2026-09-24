import Foundation
import Testing
@testable import UnlimitedKit

func claude() throws -> Reading {
    try #require(Reading.decode(Data("""
    [{"schema": 1, "vendor": "anthropic", "account": "a", "status": "ok", "taken_at": "2026-09-24T05:57:00+00:00",
      "plan": "default_claude_max_20x", "names": ["account1"],
      "credits": {"enabled": false, "used": 150.62, "limit": 150.0, "currency": "SGD",
                  "disabled_reason": "org_level_disabled_until"},
      "limits": [
       {"name": "limits:weekly_scoped:Fable", "window_minutes": 10080, "used_at_least": 0.03,
        "resets_at": "2026-09-30T06:00:00+00:00", "role": "weekly_model", "scope": "Fable"},
       {"name": "seven_day", "window_minutes": 10080, "used_at_least": 0.4, "resets_at": "2026-09-27T06:00:00+00:00",
        "role": "weekly", "projection": {"at_reset": [0.9, 1.34], "exhausts_at": "2026-09-27T01:00:00+00:00",
                                         "run_out": null, "past_windows": 2}},
       {"name": "limits:session", "window_minutes": 300, "used_at_least": 0.04, "role": null},
       {"name": "five_hour", "window_minutes": 300, "used_at_least": 0.04, "resets_at": "2026-09-24T07:15:00+00:00",
        "role": "session", "projection": {"at_reset": [0.04, 0.07], "run_out": 0.18, "past_windows": 8}}]}]
    """.utf8)).first)
}

@Test func cardsAreTheWindowsWithARoleSessionFirstAndDuplicatesLeftOut() throws {
    #expect(Card.cards(try claude(), now: now).map(\.title) == ["5-Hour", "Weekly", "Weekly · Fable"])
}

@Test func theBarsPaceTickIsHowFarThroughItsWindowWeAre() throws {
    let weekly = Card.cards(try claude(), now: now)[1]
    #expect(weekly.used == 0.4)
    #expect(abs(weekly.elapsed - 4.0 / 7) < 1e-9, "resets in 3 days of 7")
    #expect(weekly.health == .amber, "the range crosses the limit: it might run out")
}

@Test func theForecastLineSaysWhereItIsHeadingAndWhatThatRestsOn() throws {
    let tz = TimeZone(identifier: "UTC")!
    let cards = Card.cards(try claude(), now: now, timeZone: tz)
    #expect(cards[0].forecast == "→ 4–7% at reset · 18% chance of running out · from 8 past windows")
    #expect(cards[1].forecast == "→ 90–134% at reset · runs out Sun 01:00 (in 2d 19h)")
    #expect(cards[2].forecast == nil)
    #expect(cards[1].resets == "Resets in 3d 0h · Sun 06:00")
}

@Test func creditsSayWhatIsLeftToSpendAndWhetherItIsOn() throws {
    #expect(Card.credits(try claude()) == "SGD 150.62 of 150.00 · off: org_level_disabled_until")
}

@Test func creditsNeverSwitchedOnJustSayOff() throws {
    let r = try #require(Reading.decode(Data("""
    [{"schema": 1, "vendor": "anthropic", "status": "ok",
      "credits": {"enabled": false, "used": 0.0, "limit": null, "currency": "USD"}}]
    """.utf8)).first)
    #expect(Card.credits(r) == "off")
}

@Test func thePlanReadsInAPersonsWords() throws {
    #expect(Card.plan(try claude()) == "Max 20x")
}

@Test func aClickOpensTheAccountUnderThePointer() throws {
    let tiles = try Tile.strip(fixture(), now: now)  // CL1 CL2 CDX ZAI ZAI2 OPC OPC2
    let at = { (x: Double) in Tile.at(x, in: tiles, width: 26, spacing: 3, padding: 4)?.label }
    #expect(at(5) == "CL1")
    #expect(at(4 + 29 + 1) == "CL2")
    #expect(at(4 + 29 * 6 + 25) == "OPC2")
    #expect(at(0) == "CL1", "the padding before the first tile")
    #expect(at(4 + 29 * 7 + 2) == "OPC2", "the padding after the last")
    #expect(tiles.map(\.vendor).first == "anthropic")
    #expect(Tile.vendorName("zai") == "Z.ai" && Tile.vendorName("new") == "new")
}
