import AppKit
import SwiftUI
import UnlimitedKit

/// An account's description with the vendor's id for it appended, shortened, in the same text run so
/// it shares the description's baseline. The whole id is in the tooltip and copied from the menu.
struct DescribedAccount: View {
    let description: String
    let help: String
    let reading: Reading?

    var body: some View {
        text
            .help(reading?.vendorAccount.map { "\(help)\nVendor account \($0)" } ?? help)
            .contextMenu {
                if let id = reading?.vendorAccount {
                    Button("Copy Vendor Account ID") {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(id, forType: .string)
                    }
                }
            }
    }

    private var text: Text {
        guard let short = reading?.shortVendorAccount else { return Text(description) }
        return Text(description) + Text(" · ") + Text(short).monospaced()
    }
}
