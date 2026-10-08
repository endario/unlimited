import Foundation
import SwiftUI
import UnlimitedKit

struct SteeringDraft: Equatable {
    var multiplier = "1x"
    var duration = "12h"
    var untilReset = true

    init(target: String, account: String?, groups: [Runner.Incentive], now: Date = Date()) {
        guard let group = Self.group(target: target, account: account, groups: groups, now: now) else { return }
        multiplier = SteeringIndicator.format(group.multiplier)
        untilReset = group.until == nil
        if let until = group.until {
            for (suffix, seconds) in [("m", 60.0), ("h", 3600), ("d", 86_400), ("w", 604_800)] {
                let amount = max(1, ceil(until.timeIntervalSince(now) / seconds))
                if amount < 1e6 {
                    duration = "\(Int(amount))\(suffix)"
                    break
                }
            }
        }
    }

    static func group(target: String, account: String?, groups: [Runner.Incentive], now: Date = Date()) -> Runner.Incentive? {
        groups.first { group in
            group.target == target && group.account == account &&
                (group.until.map { $0 > now } ?? group.bindings.contains { binding in
                    binding.until > now && (account.map { binding.account == $0 || binding.names.contains($0) } ?? true)
                })
        }
    }
}

@MainActor
final class SteeringEditorState: ObservableObject {
    @Published var draft: SteeringDraft
    @Published var customMultiplier = false
    @Published var customDuration = false

    init(draft: SteeringDraft) { self.draft = draft }
}

struct SteeringEditor: View {
    @ObservedObject var model: StripModel
    let target: String
    let account: String?
    @StateObject private var state: SteeringEditorState
    private let multipliers = ["10x", "5x", "2x", "1x", "0.5x", "0.2x", "0.1x"]
    private let durations = ["1h", "6h", "12h", "24h", "3d", "7d"]

    init(model: StripModel, target: String, account: String?) {
        self.model = model
        self.target = target
        self.account = account
        _state = StateObject(wrappedValue: SteeringEditorState(
            draft: SteeringDraft(target: target, account: account, groups: model.incentives)))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Picker("Multiplier", selection: $state.draft.multiplier) {
                ForEach(multipliers, id: \.self) { factor in
                    Text(factor == "1x" ? "1x · Neutral override" : factor).tag(factor)
                }
                if !multipliers.contains(state.draft.multiplier) {
                    Text(state.draft.multiplier).tag(state.draft.multiplier)
                }
            }
            .accessibilityLabel("Multiplier")
            .contextMenu { Button("Custom multiplier…") { state.customMultiplier = true } }
            if state.customMultiplier {
                TextField("Custom multiplier", text: $state.draft.multiplier)
                    .accessibilityLabel("Custom multiplier")
            }
            Toggle("Until reset", isOn: $state.draft.untilReset)
                .accessibilityLabel("Until reset")
            if !state.draft.untilReset {
                Picker("Duration", selection: $state.draft.duration) {
                    ForEach(durations, id: \.self) { Text($0).tag($0) }
                    if !durations.contains(state.draft.duration) {
                        Text(state.draft.duration).tag(state.draft.duration)
                    }
                }
                .accessibilityLabel("Duration")
                .contextMenu { Button("Custom duration…") { state.customDuration = true } }
                if state.customDuration {
                    TextField("Custom duration", text: $state.draft.duration)
                        .accessibilityLabel("Custom duration")
                }
            }
            HStack {
                Button("Clear override") {
                    model.steer(target: target, multiplier: "off", account: account)
                }
                .help("Remove this scope's override; inherited policies remain.")
                .accessibilityLabel("Clear override")
                Spacer()
                if model.steeringPending { ProgressView().controlSize(.small) }
                Button("Apply") {
                    model.steer(target: target, multiplier: state.draft.multiplier, account: account,
                                duration: state.draft.untilReset ? nil : state.draft.duration)
                }
                .accessibilityLabel("Apply")
            }
            Text(account == nil ? "This target's policy. Clear keeps inherited settings." :
                 "Account override. Clear keeps inherited settings.")
                .font(.caption).foregroundStyle(.secondary)
                .help("Reset/account steering requires choose --account OFFERING=ACCOUNT (or rank accounts=).")
            if let problem = model.steeringProblem {
                Text(problem).font(.caption).foregroundStyle(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .disabled(!model.canSteer || target.isEmpty || model.steeringPending)
    }
}
