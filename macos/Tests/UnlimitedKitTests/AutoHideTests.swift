import Foundation
import Testing
@testable import UnlimitedKit

@Test func oldPreferencesKeepTheOwnersArrangementAndEnableAutoHide() throws {
    let payload = Data(#"{"labels":{"openai/a":"WORK","openai/b":"HOME"},"hidden":["openai/b"],"order":["openai/b","openai/a"]}"#.utf8)
    let prefs = try JSONDecoder().decode(Preferences.self, from: payload)
    #expect(prefs.labels == ["openai/a": "WORK", "openai/b": "HOME"])
    #expect(prefs.hidden == ["openai/b"])
    #expect(prefs.order == ["openai/b", "openai/a"])
    #expect(prefs.autoHideNormal)
}

@Test func absentPreferenceFieldsUseTheirDefaults() throws {
    let empty = try JSONDecoder().decode(Preferences.self, from: Data("{}".utf8))
    #expect(empty.labels.isEmpty)
    #expect(empty.hidden.isEmpty)
    #expect(empty.order.isEmpty)
    #expect(empty.autoHideNormal)
    #expect(empty == Preferences())
    let partial = try JSONDecoder().decode(Preferences.self, from: Data(#"{"labels":{"openai/a":"WORK"}}"#.utf8))
    #expect(partial.labels == ["openai/a": "WORK"])
    #expect(partial.hidden.isEmpty)
    #expect(partial.order.isEmpty)
    #expect(partial.autoHideNormal)
}

@Test func disablingAutoHideSurvivesPreferencesRoundTrip() throws {
    var prefs = Preferences()
    prefs.labels = ["openai/a": "WORK"]
    prefs.hidden = ["openai/b"]
    prefs.order = ["openai/b", "openai/a"]
    prefs.autoHideNormal = false
    let back = try JSONDecoder().decode(Preferences.self, from: JSONEncoder().encode(prefs))
    #expect(!back.autoHideNormal)
    #expect(back == prefs)
}

@Test func autoHideDropsQuietTilesAndKeepsVisualExceptions() {
    let normal = Tile(id: "openai/a", label: "A", value: .percent(42), dimmed: false)
    var cases: [(String, Tile, Bool)] = [("normal percentage", normal, true)]
    var best = normal
    best.best = true
    cases.append(("best underline", best, true))
    for value: Tile.Value in [.unread, .stale, .noWindow, .unknown, .waiting] {
        cases.append(("primary \(value)", Tile(id: normal.id, label: normal.label, value: value, dimmed: false), true))
    }
    var offMissing = Tile(id: normal.id, label: normal.label, value: .unread, dimmed: false)
    offMissing.off = true
    cases.append(("missing overrides off", offMissing, true))
    for health: Health in [.sprint, .underUsed, .red] {
        var tile = normal
        tile.health = health
        cases.append(("primary \(health)", tile, false))
        tile = normal
        tile.alternate = .init(role: "session", value: .percent(12), health: health)
        cases.append(("alternate \(health)", tile, false))
    }
    // Amber forecasts a limit without reaching one, so it hides like a normal reading.
    var amber = normal
    amber.health = .amber
    cases.append(("primary amber", amber, true))
    var amberAlternate = normal
    amberAlternate.alternate = .init(role: "session", value: .percent(12), health: .amber)
    cases.append(("alternate amber", amberAlternate, true))
    cases.append(("dimmed", Tile(id: normal.id, label: normal.label, value: normal.value, dimmed: true), false))
    cases.append(("prepaid balance",
                  Tile(id: normal.id, label: normal.label, value: .credit(48.09, currency: "CNY"), dimmed: false),
                  false))
    for value: Tile.Value in [.percent(100), .percent(142), .localPercent(100)] {
        cases.append(("spent \(value)",
                      Tile(id: normal.id, label: normal.label, value: value, dimmed: false, health: .red), true))
    }
    cases.append(("spent overrides the other exceptions",
                  Tile(id: normal.id, label: normal.label, value: .percent(120), dimmed: true, health: .red,
                       alternate: .init(role: "session", value: .percent(90), health: .amber)), true))
    var offSpent = Tile(id: normal.id, label: normal.label, value: .percent(120), dimmed: false, health: .red)
    offSpent.off = true
    cases.append(("spent overrides off", offSpent, true))
    cases.append(("spent overrides steering",
                  Tile(id: normal.id, label: normal.label, value: .percent(120), dimmed: false, health: .red,
                       indicator: SteeringIndicator(routes: [.init(id: "up", target: "openai", multiplier: 10,
                                                                   until: .distantFuture)],
                                                    now: Date(timeIntervalSince1970: 0))), true))
    var off = normal
    off.off = true
    cases.append(("off", off, false))
    // An incentive mark is not a usage warning: a routine account hides with one attached.
    for factors in [[2.0], [0.5], [2.0, 0.5], [1.0], []] {
        var tile = normal
        tile.indicator = SteeringIndicator(routes: factors.enumerated().map {
            .init(id: "route\($0.offset)", target: "openai", multiplier: $0.element, until: .distantFuture)
        }, now: Date(timeIntervalSince1970: 0))
        cases.append(("steering \(factors)", tile, true))
    }
    // Red stays on the strip even when steered: the warning outranks the incentive.
    var redSteered = normal
    redSteered.health = .red
    redSteered.indicator = SteeringIndicator(routes: [
        .init(id: "up", target: "openai", multiplier: 10, until: .distantFuture)
    ], now: Date(timeIntervalSince1970: 0))
    cases.append(("steering with red", redSteered, false))
    var alternate = normal
    alternate.alternate = .init(role: "session", value: .percent(12), health: .normal)
    cases.append(("normal alternate", alternate, true))
    for value: Tile.Value in [.unread, .stale, .noWindow, .unknown, .waiting] {
        alternate.alternate = .init(role: "session", value: value, health: .normal)
        cases.append(("alternate \(value)", alternate, false))
    }
    for (name, tile, expected) in cases {
        #expect(tile.isRoutine == expected, "\(name)")
    }
}
