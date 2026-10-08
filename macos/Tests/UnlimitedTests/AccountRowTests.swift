import Foundation
import Testing
@testable import Unlimited
import UnlimitedKit

private func offerReading(_ account: String?, names: [String] = []) throws -> Reading {
    let json = try JSONSerialization.data(withJSONObject: [[
        "schema": 1, "vendor": "openai", "account": account as Any? ?? NSNull(),
        "names": names, "status": "ok", "limits": []
    ]])
    return try #require(Reading.decode(json).first)
}

@Test func newBansUseCanonicalIDsEvenWhenIdentityNamesAreShared() throws {
    for id in ["first", "second"] {
        let offer = AccountOffer(reading: try offerReading(id, names: ["work"]), switches: [])
        #expect(offer.change?.target == "openai")
        #expect(offer.change?.account == id)
        #expect(offer.change?.off == true)
        #expect(!offer.requiresVendorConfirmation)
    }
}

@Test func anIdentityFreeReadingCanStillUndoItsVendorBan() throws {
    let reading = try offerReading(nil)
    let offer = AccountOffer(reading: reading, switches: [Runner.Switch(target: "openai")])
    #expect(offer.change?.target == "openai")
    #expect(offer.change?.account == nil)
    #expect(offer.change?.off == false)
    #expect(offer.row?.off == true)
    #expect(offer.requiresVendorConfirmation)
    let unbanned = AccountOffer(reading: reading, switches: [])
    #expect(unbanned.change == nil)
    #expect(!unbanned.requiresVendorConfirmation)
}

@Test func anExistingAliasBanIsUndoneWithItsStoredSpelling() throws {
    let offer = AccountOffer(reading: try offerReading("canonical", names: ["work"]),
                             switches: [Runner.Switch(target: "openai", account: "work")])
    #expect(offer.change?.account == "work")
    #expect(offer.change?.off == false)
    #expect(!offer.requiresVendorConfirmation)
}
