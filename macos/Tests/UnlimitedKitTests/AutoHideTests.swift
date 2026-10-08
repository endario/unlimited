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

@Test func routineEligibilityKeepsVisualExceptionsVisible() {
    let normal = Tile(id: "openai/a", label: "A", value: .percent(42), dimmed: false)
    var cases: [(String, Tile, Bool)] = [("normal percentage", normal, true)]
    var best = normal
    best.best = true
    cases.append(("best underline", best, true))
    for value: Tile.Value in [.unread, .stale, .noWindow, .unknown, .waiting] {
        cases.append(("primary \(value)", Tile(id: normal.id, label: normal.label, value: value, dimmed: false), false))
    }
    for health: Health in [.sprint, .underUsed, .amber, .red] {
        var tile = normal
        tile.health = health
        cases.append(("primary \(health)", tile, false))
        tile = normal
        tile.alternate = .init(role: "session", value: .percent(12), health: health)
        cases.append(("alternate \(health)", tile, false))
    }
    cases.append(("dimmed", Tile(id: normal.id, label: normal.label, value: normal.value, dimmed: true), false))
    var off = normal
    off.off = true
    cases.append(("off", off, false))
    for (factors, expected) in [([2.0], false), ([0.5], false), ([2.0, 0.5], false), ([1.0], true), ([], true)] {
        var tile = normal
        tile.indicator = SteeringIndicator(routes: factors.enumerated().map {
            .init(id: "route\($0.offset)", target: "openai", multiplier: $0.element, until: .distantFuture)
        }, now: Date(timeIntervalSince1970: 0))
        cases.append(("steering \(factors)", tile, expected))
    }
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
