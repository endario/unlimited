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
final class AccountRowState: ObservableObject {
    @Published var steering = false
    @Published var confirmVendor = false
}

struct AccountRow: View {
    @ObservedObject var model: StripModel
    let tile: Tile
    @StateObject private var state = AccountRowState()

    private var label: String { model.prefs.labels[tile.id] ?? tile.label }
    private var hidden: Bool { model.prefs.hidden.contains(tile.id) }
    private var reading: Reading? { model.readings[tile.id] }
    private var group: Runner.Incentive? {
        guard let account = reading?.account else { return nil }
        return SteeringDraft.group(target: tile.vendor, account: account, groups: model.incentives)
    }
    private var description: String {
        ([Tile.vendorName(tile.vendor)] + (reading?.names ?? [])).joined(separator: " · ")
    }

    var body: some View {
        let offer = AccountOffer(reading: reading, switches: model.offSwitches)
        HStack(spacing: 8) {
            Button { model.arrange { $0.hide(tile.id, !hidden) } } label: {
                Image(systemName: hidden ? "eye.slash" : "eye")
                    .font(.system(size: 14))
                    .foregroundStyle(hidden ? .secondary : .primary)
                    .frame(width: 26, height: 26)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .accessibilityLabel("\(hidden ? "Show" : "Hide") \(label) in menu bar")
            .accessibilityValue(hidden ? "Hidden" : "Visible")
            .help("\(hidden ? "Show" : "Hide") in menu bar. Usage remains tracked.")
            TextField("Label", text: Binding(get: { label }, set: { v in
                model.arrange { $0.rename(tile.id, to: v) }
            }))
            .labelsHidden()
            .frame(width: 44)
            .font(.system(.body, design: .monospaced))
            SteeringIndicatorView(indicator: tile.indicator)
            Text(description)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
                .help(description)
            Spacer(minLength: 0)
            controls(offer)
        }
        .opacity(model.canOffer && offer.row?.off == true ? 0.5 : 1)
    }

    private func controls(_ offer: AccountOffer) -> some View {
        HStack(spacing: 4) {
            Button { state.steering = true } label: {
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
            .disabled(!model.canSteer || reading?.account == nil || model.steeringPending)
            .accessibilityLabel("Multiplier for \(label)")
            .help(reading?.account == nil ? "No account identity is available for steering." :
                  "Edit this account's multiplier override. Selection requires account bindings (choose --account OFFERING=ACCOUNT or rank accounts=)." +
                  (group.map { " Stored override: \(SteeringIndicator.format($0.multiplier))." } ?? ""))
            .popover(isPresented: $state.steering) {
                VStack(alignment: .leading, spacing: 12) {
                    Text("\(label) · \(Tile.vendorName(tile.vendor))").font(.headline)
                    SteeringEditor(model: model, target: tile.vendor, account: reading?.account)
                }
                .padding(16)
                .frame(width: 280)
            }
            Button { flipOffer(offer) } label: {
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
            Button { model.arrange { $0.step(tile.id, by: -1) } } label: {
                Image(systemName: "chevron.up")
                    .frame(width: 26, height: 26)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .disabled(hidden)
            .accessibilityLabel("Move \(label) up")
            .help("Move \(label) up among visible accounts.")
            Button { model.arrange { $0.step(tile.id, by: 1) } } label: {
                Image(systemName: "chevron.down")
                    .frame(width: 26, height: 26)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.borderless)
            .disabled(hidden)
            .accessibilityLabel("Move \(label) down")
            .help("Move \(label) down among visible accounts.")
        }
        .fixedSize()
    }

    private func flipOffer(_ offer: AccountOffer) {
        guard let change = offer.change else { return }
        if offer.requiresVendorConfirmation { state.confirmVendor = true }
        else { model.offer(change.target, account: change.account, change.off) }
    }
}
