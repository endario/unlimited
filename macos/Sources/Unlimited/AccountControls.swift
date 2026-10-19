import SwiftUI
import UnlimitedKit

struct AccountOffer {
    let row: Tile.OfferRow?
    private let account: String?

    init(reading: Reading?, switches: [Runner.Switch]) {
        account = reading?.account
        row = reading.flatMap { Tile.offerRows(readings: [$0], switches: switches).first { $0.readHere } }
    }

    var requiresVendorConfirmation: Bool { row?.off == true && row?.account == nil }

    var change: (target: String, account: String?, off: Bool)? {
        guard let row else { return nil }
        if row.off { return (row.target, row.account, false) }
        guard let account else { return nil }
        return (row.vendor, account, true)
    }
}

@MainActor
final class AccountSteeringButtonState: ObservableObject {
    @Published var editing = false
}

@MainActor
final class AccountOfferButtonState: ObservableObject {
    @Published var confirmVendor = false
}

/// One account's steering editor, shared by the settings row and the account popover so both write
/// the same policy.
struct AccountSteeringButton: View {
    @ObservedObject var model: StripModel
    let tile: Tile
    let label: String
    @StateObject private var state = AccountSteeringButtonState()

    private var account: String? { model.readings[tile.id]?.account }
    private var group: Runner.Incentive? {
        guard let account else { return nil }
        return SteeringDraft.group(target: tile.vendor, account: account, groups: model.incentives)
    }

    var body: some View {
        Button { state.editing = true } label: {
            Group {
                if let group {
                    Text(SteeringIndicator.format(group.multiplier))
                        .font(.system(size: 12)).monospacedDigit()
                        .minimumScaleFactor(0.75)
                }
                else { Image(systemName: "slider.horizontal.3").font(.system(size: 14)) }
            }
            .lineLimit(1)
            .frame(width: 26, height: 26)
            .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .disabled(!model.canSteer || account == nil || model.steeringPending)
        .accessibilityLabel("Multiplier for \(label)")
        .help(account == nil ? "No account identity is available for steering." :
              "Edit this account's multiplier override. Selection requires account bindings (choose --account OFFERING=ACCOUNT or rank accounts=)." +
              (group.map { " Stored override: \(SteeringIndicator.format($0.multiplier))." } ?? ""))
        .popover(isPresented: $state.editing) {
            VStack(alignment: .leading, spacing: 12) {
                Text("\(label) · \(Tile.vendorName(tile.vendor))").font(.headline)
                SteeringEditor(model: model, target: tile.vendor, account: account)
            }
            .padding(16)
            .frame(width: 280)
        }
    }
}

/// One account's never-offer switch, with the whole-vendor confirmation a vendor ban needs.
struct AccountOfferButton: View {
    @ObservedObject var model: StripModel
    let tile: Tile
    let label: String
    @StateObject private var state = AccountOfferButtonState()

    private var reading: Reading? { model.readings[tile.id] }

    var body: some View {
        let offer = AccountOffer(reading: reading, switches: model.offSwitches)
        Button { flip(offer) } label: {
            Image(systemName: offer.row?.off == true ? "nosign" : "circle.slash")
                .font(.system(size: 14))
                .frame(width: 26, height: 26)
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .disabled(!model.canOffer || offer.change == nil || model.steeringPending)
        .accessibilityLabel("\(offer.row?.off == true ? "Allow" : "Ban") \(label)")
        .accessibilityValue(!model.canOffer ? "Unavailable" : offer.row?.off == true ? "Banned" : "Allowed")
        .help(offer.row?.account == nil && offer.row?.off == true ?
              "Banned by vendor policy. Allowing affects all \(Tile.vendorName(tile.vendor)) accounts; account bans may remain." :
              "\(offer.row?.off == true ? "Allow" : "Never offer") this account. Usage remains tracked.")
        .confirmationDialog("Allow all \(Tile.vendorName(tile.vendor)) accounts?", isPresented: $state.confirmVendor) {
            Button("Allow all \(Tile.vendorName(tile.vendor)) accounts") {
                let current = AccountOffer(reading: reading, switches: model.offSwitches)
                if current.requiresVendorConfirmation, let change = current.change {
                    model.offer(change.target, account: nil, false)
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This removes the whole-vendor ban. Separate account bans remain.")
        }
    }

    private func flip(_ offer: AccountOffer) {
        guard let change = offer.change else { return }
        if offer.requiresVendorConfirmation { state.confirmVendor = true }
        else { model.offer(change.target, account: change.account, change.off) }
    }
}
