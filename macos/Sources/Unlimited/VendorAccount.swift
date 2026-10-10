import SwiftUI
import UnlimitedKit

/// An account's description with the vendor's id for it appended, shortened, in the same text run so
/// it shares the description's baseline; the whole id goes in `vendorAccountHelp`.
func describing(_ description: String, _ reading: Reading?) -> Text {
    guard let short = reading?.shortVendorAccount else { return Text(description) }
    return Text(description) + Text(" · ") + Text(short).monospaced()
}

func vendorAccountHelp(_ help: String, _ reading: Reading?) -> String {
    reading?.vendorAccount.map { "\(help)\nVendor account \($0)" } ?? help
}
