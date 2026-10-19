import SwiftUI
import UnlimitedKit

struct AccountRow: View {
    @ObservedObject var model: StripModel
    let tile: Tile

    private var label: String { model.name(of: tile) }
    private var hidden: Bool { model.prefs.hidden.contains(tile.id) }
    private var reading: Reading? { model.readings[tile.id] }
    private var description: String {
        ([Tile.vendorName(tile.vendor)] + (reading?.names ?? [])).joined(separator: " · ")
    }
    private var figureWidth: CGFloat {
        max(32, CGFloat(StripLayout(tiles: [tile], spacing: 0, padding: 0).width(of: tile)))
    }

    var body: some View {
        let offer = AccountOffer(reading: reading, switches: model.offSwitches)
        let usage = UsageFigure(value: tile.value, health: tile.health, percentSign: true)
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
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
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    HStack(spacing: 1) {
                        Text(label)
                            .hidden()
                            .overlay {
                                TextField("Label", text: Binding(get: { label }, set: { v in
                                    model.arrange { $0.rename(tile.id, to: v) }
                                }))
                                .labelsHidden()
                                .textFieldStyle(.plain)
                            }
                            .font(.system(.body, design: .monospaced))
                            .lineLimit(1)
                            .frame(maxWidth: 40, alignment: .leading)
                            .fixedSize(horizontal: true, vertical: false)
                        SteeringIndicatorView(indicator: tile.indicator)
                    }
                    .frame(width: 48, alignment: .leading)
                    usage
                        .font(.system(.body, design: .rounded).weight(.semibold))
                        .lineLimit(1)
                        .fixedSize()
                        .frame(width: figureWidth, alignment: .center)
                        .opacity(tile.dimmed ? 0.45 : 1)
                        .accessibilityLabel("Usage for \(label)")
                        .accessibilityValue(usage.text)
                        .help(reading?.primary?.name ?? "Account usage")
                }
                DescribedAccount(description: description, help: description, reading: reading)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 0)
                controls()
            }
            if let measured = reading?.usage { LoggedUsageView(usage: measured) }
        }
        .opacity(model.canOffer && offer.row?.off == true ? 0.5 : 1)
    }

    private func controls() -> some View {
        HStack(spacing: 4) {
            AccountSteeringButton(model: model, tile: tile, label: label)
            AccountOfferButton(model: model, tile: tile, label: label)
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
}
