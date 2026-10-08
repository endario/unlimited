import Foundation
import Testing
@testable import UnlimitedKit

func tiles() throws -> [Tile] { try Tile.strip(fixture(), now: now) }  // CL1 CL2 CDX ZAI ZAI2 OPC OPC2

@Test func aNewAccountKeepsTheLabelItWasFirstSeenWithEvenWhenAnotherArrives() throws {
    var prefs = Preferences()
    let first = prefs.apply(try tiles())
    #expect(first.map(\.label) == ["CL1", "CL2", "CDX", "ZAI", "ZAI2", "OPC", "OPC2"])
    // Codex gains a second, unnamed account, and the strip renumbers CDX to CDX1 and CDX2.
    let more = try tiles().map { $0.id == "openai/c" ? Tile(id: $0.id, label: "CDX1", value: $0.value, dimmed: false) : $0 }
        + [Tile(id: "openai/zz", label: "CDX2", value: .percent(1), dimmed: false)]
    let second = prefs.apply(more)
    #expect(second.first { $0.id == "openai/c" }?.label == "CDX", "a label, once shown, stays")
    #expect(second.last?.id == "openai/zz", "a new account joins at the end")
}

@Test func theOwnerCanRelabelHideAndReorder() throws {
    var prefs = Preferences()
    _ = prefs.apply(try tiles())
    prefs.rename("zai/z1", to: "glm1x")
    prefs.hide("anthropic/a1", true)
    prefs.move("opencode/o1", to: 0)
    let got = prefs.apply(try tiles()).map(\.label)
    #expect(got.first == "OPC")
    #expect(!got.contains("CL1"))
    #expect(got.contains("GLM1"), "four characters at most, upper case")
}

@Test func stepsSkipHiddenAccountsSoOnePressAlwaysMovesAVisibleOne() throws {
    var prefs = Preferences()
    _ = prefs.apply(try tiles())            // CL1 CL2 CDX ...
    prefs.hide("anthropic/a2", true)       // CL2
    prefs.step("anthropic/a1", by: 1)
    #expect(prefs.apply(try tiles()).prefix(2).map(\.label) == ["CDX", "CL1"])
    prefs.step("anthropic/a1", by: -1)
    #expect(prefs.apply(try tiles()).prefix(2).map(\.label) == ["CL1", "CDX"])
}

@Test func aLongAccountListReordersVisibleAndBannedRowsAndSurvivesRelaunch() throws {
    let ids = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"]
    let accounts = ids.map { id in
        var tile = Tile(id: id, label: id.uppercased(), value: .percent(10), dimmed: false)
        tile.off = id == "h"
        return tile
    }
    var prefs = Preferences()
    _ = prefs.apply(accounts)
    for id in ["b", "g", "k"] { prefs.hide(id, true) }
    #expect(prefs.apply(accounts).map(\.id) == ["a", "c", "d", "e", "f", "h", "i", "j", "l"])

    prefs.step("a", by: -1)
    prefs.step("l", by: 1)
    #expect(prefs.apply(accounts).map(\.id) == ["a", "c", "d", "e", "f", "h", "i", "j", "l"])
    prefs.step("a", by: 1)
    prefs.step("a", by: 1)
    prefs.step("l", by: -1)
    prefs.step("h", by: -1)
    #expect(prefs.apply(accounts).map(\.id) == ["c", "d", "a", "e", "h", "f", "i", "l", "j"])
    #expect(prefs.apply(accounts).first { $0.id == "h" }?.off == true)
    let beforeHiddenMove = prefs.order
    prefs.step("g", by: 1)
    #expect(prefs.order == beforeHiddenMove)

    var back = try JSONDecoder().decode(Preferences.self, from: JSONEncoder().encode(prefs))
    #expect(back.apply(accounts).map(\.id) == ["c", "d", "a", "e", "h", "f", "i", "l", "j"])
    #expect(back.hidden == ["b", "g", "k"])
}

@Test func preferencesSurviveARoundTrip() throws {
    var prefs = Preferences()
    _ = prefs.apply(try tiles())
    prefs.hide("anthropic/a1", true)
    let back = try JSONDecoder().decode(Preferences.self, from: JSONEncoder().encode(prefs))
    #expect(back == prefs)
}
