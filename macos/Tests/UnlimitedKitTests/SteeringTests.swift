import Foundation
import Testing
@testable import UnlimitedKit

@Test func olderReadingsDecodeWithoutSteering() throws {
    let reading = try #require(try Reading.decode(Data(#"[{"schema":1,"vendor":"openai","account":"a","status":"ok"}]"#.utf8)).first)
    #expect(reading.steering.settings.isEmpty)
    #expect(reading.steering.routes.isEmpty)
}

@Test func partialSteeringOverlayDecodesMissingListsAsEmpty() throws {
    let reading = try #require(try Reading.decode(Data(#"[{"schema":1,"vendor":"openai","status":"ok","steering":{"settings":[{"target":"openai","multiplier":3,"until":"2026-10-03T00:00:00Z"}]}}]"#.utf8)).first)
    #expect(reading.steering.settings.count == 1)
    #expect(reading.steering.routes.isEmpty)
}

@Test func activeRouteWinnersSummarizeBothDirectionsAtReciprocalThresholds() throws {
    let readings = try Reading.decode(Data(#"[{"schema":1,"vendor":"openai","account":"a","status":"ok","steering":{"settings":[],"routes":[{"id":"up","target":"openai","account":null,"multiplier":10,"until":"2026-10-03T00:00:00Z"},{"id":"down","target":"gpt","account":null,"multiplier":0.1,"until":"2026-10-03T00:00:00Z"},{"id":"expired","target":"old","account":null,"multiplier":100,"until":"2026-10-01T00:00:00Z"}]}}]"#.utf8))
    let tile = try #require(Tile.strip(readings, now: Date(timeIntervalSince1970: 1_790_870_400)).first)
    #expect(tile.indicator?.up?.triangles == 3)
    #expect(tile.indicator?.down?.triangles == 3)
    #expect(tile.indicator?.tooltip.contains("10x") == true)
    #expect(tile.indicator?.tooltip.contains("0.1x") == true)
}

@Test func neutralRouteOverrideHasNoIndicator() throws {
    let readings = try Reading.decode(Data(#"[{"schema":1,"vendor":"openai","account":"a","status":"ok","steering":{"settings":[],"routes":[{"id":"neutral","target":"openai","account":null,"multiplier":1,"until":"2026-10-03T00:00:00Z"}]}}]"#.utf8))
    #expect(Tile.strip(readings, now: Date(timeIntervalSince1970: 1_790_870_400)).first?.indicator == nil)
}

@Test func labelledTileRetainsItsSteeringIndicator() throws {
    let indicator = SteeringIndicator(routes: [.init(id: "r", target: "openai", multiplier: 5, until: Date.distantFuture)], now: .now)
    let tile = Tile(id: "openai/a", label: "CDX", value: .percent(10), dimmed: false, indicator: indicator)
    #expect(tile.labelled("Owner").indicator == indicator)
}

@Test func layoutUsesWiderCellsForIndicatorsAndBoundaryHitTesting() {
    let plain = Tile(id: "a", label: "A", value: .percent(1), dimmed: false)
    let steered = Tile(id: "b", label: "B", value: .percent(1), dimmed: false,
                       indicator: SteeringIndicator(routes: [.init(id: "r", target: "x", multiplier: 10, until: .distantFuture)], now: .now))
    let layout = StripLayout(tiles: [plain, steered], spacing: 3, padding: 2)
    #expect(layout.width(of: plain) == 26)
    #expect(layout.width(of: steered) > layout.width(of: plain))
    #expect(layout.tile(at: layout.rect(of: steered).minX - 0.01)?.id == "a")
    #expect(layout.tile(at: layout.rect(of: steered).minX)?.id == "b")
    #expect(layout.rect(of: steered).width == layout.width(of: steered))
}

@Test func runnerBuildsSetAndClearArguments() {
    #expect(Runner.incentiveArguments(target: "openai", multiplier: "10x", account: "owner", duration: "12h") == ["incentive", "openai", "10x", "--account", "owner", "--for", "12h"])
    #expect(Runner.incentiveArguments(target: "openai", multiplier: "off", account: nil, duration: nil) == ["incentive", "openai", "off"])
}

@Test func runnerSurfacesSteeringCommandFailure() {
    let runner = Runner(binary: URL(filePath: "/usr/bin/false"))
    #expect(throws: Runner.RunError.self) {
        try runner.setIncentive("openai", multiplier: "10x")
    }
}

@Test func integralHugeMultiplierFormatsWithoutIntegerConversion() {
    #expect(SteeringIndicator.format(1e20) == "1e+20x")
    for factor in [Double.leastNonzeroMagnitude, 1e20, Double.greatestFiniteMagnitude, 1.0000000000000002] {
        #expect(Double(SteeringIndicator.format(factor).dropLast()) == factor)
    }
}

@Test func neutralAndInvalidStrengthsDoNotDrawArrowheads() {
    for factor in [1, 0, -1, Double.infinity, Double.nan] {
        #expect(SteeringIndicator.triangles(for: factor) == 0)
    }
}

@Test func strengthThresholdsAreReciprocal() {
    for (factor, count) in [(2.0, 1), (4.99, 1), (5, 2), (9.99, 2), (10, 3), (1e20, 3)] {
        #expect(SteeringIndicator.triangles(for: factor) == count)
        #expect(SteeringIndicator.triangles(for: 1 / factor) == count)
    }
}

@Test func tooltipRetainsExactRouteAccountFactorAndExpiry() {
    let indicator = SteeringIndicator(routes: [.init(id: "route-a", target: "model-a", account: "owner", multiplier: 1.2345678901234567, until: Date(timeIntervalSince1970: 1_790_985_600))], now: Date(timeIntervalSince1970: 0))
    #expect(indicator.tooltip.contains("route-a"))
    #expect(indicator.tooltip.contains("model-a"))
    #expect(indicator.tooltip.contains("owner"))
    #expect(indicator.tooltip.contains("1.2345678901234567x"))
    #expect(indicator.tooltip.contains("2026-10-03T00:00:00Z"))
}

@Test func everyStrengthReservesOneGlyphPerDirection() {
    for factor in [2.0, 5, 10, 0.5, 0.2, 0.1] {
        let tile = Tile(id: "a", label: "A", value: .percent(1), dimmed: false,
                        indicator: SteeringIndicator(routes: [.init(id: "r", target: "x", multiplier: factor, until: .distantFuture)], now: .now))
        #expect(StripLayout(tiles: [tile], spacing: 3, padding: 2).width(of: tile) == 33)
    }
}

@Test func fixedWidthHitTestingStillHonorsItsWidthArgument() {
    let tiles = [Tile(id: "a", label: "A", value: .percent(1), dimmed: false),
                 Tile(id: "b", label: "B", value: .percent(1), dimmed: false)]
    #expect(Tile.at(40, in: tiles, width: 40, spacing: 3, padding: 2)?.id == "a")
}

@Test func pendingCommandCannotBeSupersededByRead() throws {
    var fence = SteeringPublicationFence()
    let start = fence.beginCommand()
    let command = try #require(start)
    let read = fence.beginRead()
    #expect(read == nil)
    let duplicate = fence.beginCommand()
    #expect(duplicate == nil)
    #expect(fence.accepts(command))
    let finished = fence.finish(command)
    #expect(finished)
    #expect(!fence.pending)
}

@Test func groupDecodingPreservesActivationAndResetBindings() throws {
    let groups = try Runner.decodeIncentives(Data(#"[{"target":"openai/gpt-6","account":null,"multiplier":10,"activated_at":"2026-10-03T00:00:00Z","until":null,"bindings":[{"vendor":"openai","account":"owner","names":["account1"],"until":"2026-10-04T00:00:00Z"}]}]"#.utf8))
    let group = try #require(groups.first)
    #expect(group.activatedAt == Date(timeIntervalSince1970: 1_790_985_600))
    #expect(group.bindings.first?.account == "owner")
    #expect(group.bindings.first?.names == ["account1"])
}

@Test func publicationFenceRejectsPreMutationReadAndFinishesCommand() throws {
    var fence = SteeringPublicationFence()
    let readStart = fence.beginRead()
    let staleRead = try #require(readStart)
    let started = fence.beginCommand()
    let command = try #require(started)
    #expect(!fence.accepts(staleRead))
    let finished = fence.finish(command)
    #expect(finished)
    #expect(!fence.pending)
}
