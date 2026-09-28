import Foundation
import Testing
@testable import UnlimitedKit

@Test func rowsCoverAccountsAndWholeVendorSwitches() {
    let rows = Tile.offerRows(readings: [
        account("anthropic", id: "a1", names: ["account1"]),
        account("zai", id: "4ff9", names: ["claude-glm-2"]),
        account("zai", id: "da68", names: ["claude-glm"]),
    ], switches: [Runner.Switch(target: "kimi")])
    #expect(rows.map(\.ident) == ["account1", "claude-glm", "claude-glm-2", "kimi"])
    #expect(rows.map(\.off) == [false, false, false, true])
    #expect(rows.map(\.readHere) == [true, true, true, false])
    #expect(rows[3].target == "kimi")
    #expect(rows[3].account == nil, "a whole-vendor switch flips by the vendor alone")
}

@Test func anAccountSwitchOffsOnlyItsOwnRow() {
    let rows = Tile.offerRows(readings: [
        account("zai", id: "4ff9", names: ["claude-glm-2"]),
        account("zai", id: "da68", names: ["claude-glm"]),
    ], switches: [Runner.Switch(target: "zai", account: "claude-glm-2")])
    #expect(rows.map(\.ident) == ["claude-glm", "claude-glm-2"])
    #expect(rows.map(\.off) == [false, true])
    #expect(rows[1].account == "claude-glm-2")
}

@Test func aVendorSwitchRowFlipsTheVendorNotAnAccount() {
    let rows = Tile.offerRows(readings: [account("kimi", id: "k1", names: ["claude-kimi-1"])],
                              switches: [Runner.Switch(target: "kimi")])
    #expect(rows.map(\.off) == [true])
    #expect(rows[0].account == nil, "`on` names the vendor, or the CLI refuses it")
}

@Test func aSwitchWrittenByIdIsUndoneById() {
    let rows = Tile.offerRows(readings: [account("zai", id: "4ff9f720", names: ["claude-glm-2"])],
                              switches: [Runner.Switch(target: "zai", account: "4ff9f720")])
    #expect(rows.map(\.off) == [true])
    #expect(rows[0].account == "4ff9f720", "the flip names the account the switch names")
}

@Test func aSwitchWhoseTargetCarriesSlipsShowsWhole() {
    let rows = Tile.offerRows(readings: [], switches: [
        Runner.Switch(target: "commandcode/meta/muse-spark-1.3-contributor"),
    ])
    #expect(rows.map(\.ident) == ["commandcode/meta/muse-spark-1.3-contributor"])
    #expect(rows[0].account == nil, "an offering switch is not split into vendor and account")
    #expect(rows[0].readHere == false)
}

private func account(_ vendor: String, id: String, names: [String]) -> Reading {
    try! Reading.decode(Data("""
    [{"schema": 1, "vendor": "\(vendor)", "account": "\(id)", "names": \(rawJSON(names)),
      "status": "ok", "limits": []}]
    """.utf8))[0]
}

private func rawJSON(_ strings: [String]) -> String {
    "[" + strings.map { "\"\($0)\"" }.joined(separator: ", ") + "]"
}
