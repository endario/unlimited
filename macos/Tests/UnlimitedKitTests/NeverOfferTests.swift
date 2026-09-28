import Foundation
import Testing
@testable import UnlimitedKit

@Test func rowsCoverAccountsAndWholeVendorSwitches() {
    let rows = Tile.offerRows(readings: [
        account("anthropic", id: "a1", names: ["account1"]),
        account("zai", id: "4ff9", names: ["claude-glm-2"]),
        account("zai", id: "da68", names: ["claude-glm"]),
    ], off: ["kimi"])
    #expect(rows.map(\.ident) == ["account1", "claude-glm", "claude-glm-2", "kimi"])
    #expect(rows.map(\.off) == [false, false, false, true])
    #expect(rows.map(\.readHere) == [true, true, true, false])
}

@Test func anAccountSwitchOffsOnlyItsOwnRow() {
    let rows = Tile.offerRows(readings: [
        account("zai", id: "4ff9", names: ["claude-glm-2"]),
        account("zai", id: "da68", names: ["claude-glm"]),
    ], off: ["zai/claude-glm-2"])
    #expect(rows.map(\.ident) == ["claude-glm", "claude-glm-2"])
    #expect(rows.map(\.off) == [false, true])
}

@Test func aWholeVendorSwitchOffsAllItsRows() {
    let rows = Tile.offerRows(readings: [
        account("zai", id: "4ff9", names: ["claude-glm-2"]),
        account("zai", id: "da68", names: ["claude-glm"]),
    ], off: ["zai"])
    #expect(rows.map(\.off) == [true, true])
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
