import Foundation
import Testing
@testable import UnlimitedKit

@Test func rowsCoverAccountsAndSwitchesBoth() {
    let rows = Tile.neverOffer(off: ["kimi"], vendors: ["anthropic", "kimi", "zai", "zai"])
    #expect(rows.map(\.vendor) == ["anthropic", "zai", "kimi"])  // strip order
    #expect(rows.map(\.off) == [false, false, true])
    #expect(rows.map(\.accountHere) == [true, true, true])
}

@Test func rowsShowASwitchWithNoAccountHere() {
    let rows = Tile.neverOffer(off: ["neuralwatt"], vendors: ["kimi"])
    #expect(rows.map(\.vendor) == ["kimi", "neuralwatt"])
    #expect(rows.map(\.accountHere) == [true, false])
    #expect(rows.last?.off == true)
}
