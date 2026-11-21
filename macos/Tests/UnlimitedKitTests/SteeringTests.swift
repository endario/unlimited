import Foundation
import Testing
@testable import UnlimitedKit

@Test func olderReadingsDecodeWithoutSteering() throws {
    let reading = try #require(try Reading.decode(Data(#"[{"schema":1,"vendor":"openai","account":"a","status":"ok"}]"#.utf8)).first)
    #expect(reading.steering.rates.isEmpty)
}

@Test func rateSpansDecodeWithTheirWindows() throws {
    let reading = try #require(try Reading.decode(Data(#"[{"schema":1,"vendor":"zai","status":"ok","steering":{"rates":[{"offerings":["opencode-go/deepseek-v4.1-flash"],"from":"2026-09-24T06:00:00+00:00","to":"2026-09-24T10:00:00.500000+00:00","factor":2.5}]}}]"#.utf8)).first)
    let rate = try #require(reading.steering.rates.first)
    #expect(rate.offerings == ["opencode-go/deepseek-v4.1-flash"])
    #expect(rate.from == Date(timeIntervalSince1970: 1_790_229_600))
    #expect(rate.to == Date(timeIntervalSince1970: 1_790_244_000.5))
    #expect(rate.factor == 2.5)
}

@Test func aStampCarryingIncentivesStillDecodesAndDrawsNoWiderTile() throws {
    let readings = try Reading.decode(Data(#"[{"schema":1,"vendor":"openai","account":"a","status":"ok","steering":{"settings":[{"target":"openai","multiplier":3,"until":"2030-01-01T00:00:00Z"}],"routes":[{"id":"own","target":"openai","account":"a","multiplier":10,"until":"2030-01-01T00:00:00Z"}]}}]"#.utf8))
    let tile = try #require(Tile.strip(readings, now: Date(timeIntervalSince1970: 1_790_870_400)).first)
    #expect(readings.first?.steering.rates.isEmpty == true)
    #expect(StripLayout(tiles: [tile], spacing: 3, padding: 2).width(of: tile) == StripLayout.neutralWidth)
}

@Test func layoutHitTestingHonorsEachTilesBoundary() {
    let a = Tile(id: "a", label: "A", value: .percent(1), dimmed: false)
    let b = Tile(id: "b", label: "B", value: .percent(1), dimmed: false)
    let layout = StripLayout(tiles: [a, b], spacing: 3, padding: 2)
    #expect(layout.width(of: a) == 21)
    #expect(layout.tile(at: layout.rect(of: b).minX - 0.01)?.id == "a")
    #expect(layout.tile(at: layout.rect(of: b).minX)?.id == "b")
    #expect(layout.rect(of: b).width == layout.width(of: b))
}

@Test func fixedWidthHitTestingStillHonorsItsWidthArgument() {
    let tiles = [Tile(id: "a", label: "A", value: .percent(1), dimmed: false),
                 Tile(id: "b", label: "B", value: .percent(1), dimmed: false)]
    #expect(Tile.at(40, in: tiles, width: 40, spacing: 3, padding: 2)?.id == "a")
}
