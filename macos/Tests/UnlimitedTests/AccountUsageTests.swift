import AppKit
import SwiftUI
import Testing
@testable import Unlimited
@testable import UnlimitedKit

@MainActor
private func usageImage(_ tile: Tile) throws -> NSBitmapImageRep {
    _ = NSApplication.shared
    let defaults = try #require(UserDefaults(suiteName: "AccountUsageTests-\(UUID())"))
    let host = NSHostingView(rootView: AccountRow(model: StripModel(defaults: defaults), tile: tile)
        .padding(10).frame(width: 560, height: 46).environment(\.colorScheme, .dark))
    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 46),
                          styleMask: [.borderless], backing: .buffered, defer: false)
    window.contentView = host
    host.layoutSubtreeIfNeeded()
    let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
    host.cacheDisplay(in: host.bounds, to: image)
    return image
}

@MainActor
@Test func usageFiguresAppendPercentOnlyToNumbers() {
    for (value, text) in [(Tile.Value.percent(0), "0%"), (.percent(99), "99%"),
                          (.percent(100), "✘"), (.percent(142), "✘"), (.localPercent(100), "✘"),
                          (.credit(48.09, currency: "CNY"), "48"),
                          (.unread, "?"), (.stale, "?"), (.unknown, "?"),
                          (.noWindow, "—"), (.waiting, "…")] {
        #expect(UsageFigure(value: value, health: .normal, percentSign: true).text == text)
        #expect(UsageFigure(value: value, health: .normal).text == value.text)
    }
}

private func greenColumns(_ image: NSBitmapImageRep) throws -> [Int] {
    var columns: [Int] = []
    for y in 0..<image.pixelsHigh {
        for x in 0..<image.pixelsWide {
            let c = try #require(image.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
            if c.greenComponent > 0.7 && c.greenComponent > c.redComponent * 1.3 &&
                c.greenComponent > c.blueComponent * 1.2 { columns.append(x) }
        }
    }
    return columns
}

@MainActor
@Test func incentiveArrowSitsImmediatelyAfterAliasInk() throws {
    let indicator = SteeringIndicator(routes: [.init(id: "up", target: "openai", multiplier: 10,
                                                    until: .distantFuture)], now: .now)
    let image = try usageImage(Tile(id: "openai/test", label: "CDX", value: .percent(27),
                                    dimmed: false, indicator: indicator))
    let arrowStart = try #require(try greenColumns(image).min())
    var aliasEnd: Int?
    for y in 0..<image.pixelsHigh {
        for x in 0..<arrowStart {
            let c = try #require(image.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
            if c.alphaComponent > 0.7 && min(c.redComponent, c.greenComponent, c.blueComponent) > 0.7 {
                aliasEnd = max(aliasEnd ?? 0, x)
            }
        }
    }
    let gap = arrowStart - (try #require(aliasEnd)) - 1
    #expect(gap <= 3 * image.pixelsWide / 560)
}

@MainActor
@Test func theSpentMarkStandsAsTallAsTheFiguresItReplaces() throws {
    let digits = try bitmap(UsageFigure(value: .percent(27), health: .red)
        .font(TileView.valueFont).monospacedDigit().fixedSize(), width: 60, height: 22)
    let mark = try bitmap(UsageFigure(value: .percent(120), health: .red)
        .font(TileView.valueFont).monospacedDigit().fixedSize(), width: 60, height: 22)
    let digitInk = try inkBounds(digits)
    let markInk = try inkBounds(mark)
    print("Spent-mark ink heights (pixels): digits=\(digitInk.height), mark=\(markInk.height)")
    #expect(abs(digitInk.height - markInk.height) <= 1, "the cross stands as tall as the figures it replaces")
}

@MainActor
@Test func settingsPercentageColumnKeepsDifferentDigitCountsCentred() throws {
    var centers: [Int] = []
    for value in [Tile.Value.percent(27), .percent(100), .localPercent(25), .localPercent(100)] {
        let image = try usageImage(Tile(id: "openai/test", label: "CDX", value: value,
                                        dimmed: false, health: .underUsed))
        let columns = try greenColumns(image)
        centers.append(try #require(columns.min()) + #require(columns.max()))
    }
    print("Percentage ink centers (pixels): \(centers.map { Double($0) / 2 })")
    #expect(centers.allSatisfy { abs($0 - centers[0]) <= 2 })
}

@MainActor
@Test func settingsDetailsExplainLoggedCoverageAndCollectionAge() throws {
    let usage = try #require(try Reading.decode(Data("""
    [{"schema": 1, "vendor": "woyo", "status": "ok", "limits": [],
      "usage": {"source": "gateway-log", "as_of": "2026-09-24T06:00:00+00:00", "collected_at": "2026-09-24T05:59:00+00:00", "periods": [
        {"window_minutes": 10080, "starts_at": "2026-09-17T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 2851, "output_tokens": 85, "total_tokens": 2936, "cache_read_tokens": 1088, "cache_creation_tokens": 0, "coverage": "partial", "reasons": ["horizon-edge"]},
        {"window_minutes": 43200, "starts_at": "2026-08-25T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 2851, "output_tokens": 85, "total_tokens": 2936, "cache_read_tokens": 1088, "cache_creation_tokens": 0, "coverage": "partial", "reasons": ["horizon-edge"]}
      ]}}]
    """.utf8))[0].usage)
    let detail = LoggedUsageDetail(usage: usage, now: Date(timeIntervalSince1970: 1_790_229_600))

    #expect(detail.source == "Observed from gateway log · collected 1m ago")
    #expect(detail.periods.map(\.title) == ["Rolling 7d", "Rolling 30d"])
    #expect(detail.periods.map(\.total) == ["2,936 tokens", "2,936 tokens"])
    #expect(detail.periods.allSatisfy { $0.coverage == "Partial coverage · history begins at the observation horizon" })
    #expect(detail.snapshot == "Aggregate snapshot: 2026-09-24 06:00 UTC")
    #expect(detail.periods.map(\.range) == ["2026-09-17 06:00–2026-09-24 06:00 UTC", "2026-08-25 06:00–2026-09-24 06:00 UTC"])
}

@MainActor
@Test func measuredUsageDiagnosticExplainsUnreadableLocalState() throws {
    let usage = try #require(try Reading.decode(Data("""
    [{"schema": 1, "vendor": "woyo", "status": "ok", "limits": [],
      "usage": {"source": "gateway-log", "as_of": "2026-09-24T06:00:00+00:00", "collected_at": null,
        "diagnostic": "state-unreadable", "periods": [
          {"window_minutes": 10080, "starts_at": "2026-09-17T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": null, "output_tokens": null, "total_tokens": null, "cache_read_tokens": null, "cache_creation_tokens": null, "coverage": "unknown", "reasons": []},
          {"window_minutes": 43200, "starts_at": "2026-08-25T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": null, "output_tokens": null, "total_tokens": null, "cache_read_tokens": null, "cache_creation_tokens": null, "coverage": "unknown", "reasons": []}
        ]}}]
    """.utf8))[0].usage)

    #expect(LoggedUsageDetail(usage: usage, now: Date(timeIntervalSince1970: 1_790_229_600)).diagnostic ==
            "Local logged-usage state cannot be read. Totals are unavailable until it is repaired.")
}

@MainActor
@Test func localPacingShowsProgressWithoutCallingPartialHistoryRoom() throws {
    let usage = try #require(try Reading.decode(Data("""
    [{"schema": 1, "vendor": "woyo", "status": "ok", "limits": [],
      "usage": {"source": "gateway-log", "as_of": "2026-09-24T06:00:00+00:00", "collected_at": "2026-09-24T05:59:00+00:00", "periods": [
        {"window_minutes": 10080, "starts_at": "2026-09-17T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 12000, "output_tokens": 500, "total_tokens": 12500, "cache_read_tokens": 0, "cache_creation_tokens": 0, "coverage": "partial", "reasons": ["horizon-edge"], "local_budget": {"status": "ok", "token_limit": 10000, "profiles": ["gateway"]}}
      ]}}]
    """.utf8))[0].usage)

    let period = try #require(LoggedUsageDetail(usage: usage, now: .now).periods.first)

    #expect(period.localPacing == "Local pacing: 12,500 / 10,000 tokens (125.0%) · partial history · profiles gateway")
}

@MainActor
@Test func localPacingKeepsUnknownTotalsAndConfigurationDiagnosticsExplicit() throws {
    let usage = try #require(try Reading.decode(Data("""
    [{"schema": 1, "vendor": "woyo", "status": "ok", "limits": [],
      "usage": {"source": "gateway-log", "as_of": "2026-09-24T06:00:00+00:00", "collected_at": null, "periods": [
        {"window_minutes": 10080, "starts_at": "2026-09-17T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": null, "output_tokens": null, "total_tokens": null, "cache_read_tokens": null, "cache_creation_tokens": null, "coverage": "unknown", "reasons": [], "local_budget": {"status": "ok", "token_limit": 10000, "profiles": ["gateway"]}},
        {"window_minutes": 43200, "starts_at": "2026-08-25T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "coverage": "observed", "reasons": [], "local_budget": {"status": "conflict", "token_limit": null, "profiles": ["one", "two"]}}
      ]}}]
    """.utf8))[0].usage)

    let periods = LoggedUsageDetail(usage: usage, now: .now).periods

    #expect(periods[0].localPacing == "Local pacing: 10,000-token limit · total unknown · history unknown · profiles gateway")
    #expect(periods[1].localPacing == "Local pacing: configuration conflict · profiles one, two")
}

@MainActor
@Test func quotaFactsStaySuppressedAfterAFailedRefresh() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let now = Date(timeIntervalSince1970: 1_790_985_600)
    try cli.succeed(takenAt: now, used: 0.42)
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { now })
    model.refresh()
    try await waitUntil { model.accounts.count == 1 }
    let reading = try #require(model.readings["openai/fixture"])
    #expect(!Card.cards(reading, now: now).isEmpty, "the fixture must have a quota card to suppress")
    try cli.fail()
    model.refresh()
    try await waitUntil { model.problem != nil }

    #expect(!PopoverContent.showsQuotaFacts(problem: model.problem))
}

@MainActor
@Test func popoverKeepsTheHardFailureReasonAndMeasuredSnapshot() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let now = Date(timeIntervalSince1970: 1_790_229_600)
    let aggregateAt = now.addingTimeInterval(-86_400)
    try cli.succeedMeasured(takenAt: now, asOf: aggregateAt)
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { now })
    model.refresh()
    try await waitUntil { model.accounts.count == 1 }
    model.selected = "woyo/synthetic"
    try cli.unsupportedSchema()
    model.refresh()
    try await waitUntil { model.tiles == [.broken] && model.problem != nil }

    let failure = try #require(PopoverContent.failure(problem: model.problem, readings: model.readings, selected: model.selected))
    #expect(failure.message == model.problem)
    #expect(failure.usage?.asOf == aggregateAt)
    #expect(failure.usage?.collectedAt == now)
    #expect(failure.usage?.period(windowMinutes: 10_080)?.totalTokens == 2936)
}

@MainActor
@Test func popoverKeepsAnInitialHardFailureReasonWithoutAReading() async throws {
    let runner = Runner(binary: URL(filePath: "/nonexistent/unlimited"))
    let model = StripModel(runner: runner, defaults: try #require(UserDefaults(suiteName: "initial-hard-failure-\(UUID())")))
    model.refresh()
    try await waitUntil { model.tiles == [.broken] && model.problem != nil }

    let failure = try #require(PopoverContent.failure(problem: model.problem, readings: model.readings, selected: model.selected))
    #expect(failure.message == model.problem)
    #expect(failure.usage == nil)
}

@MainActor
@Test func failureNamesTheSelectedAccountNeverAnotherWithUsage() throws {
    let readings = try Reading.decode(Data("""
    [{"schema":1,"vendor":"openai","account":"quota","status":"ok","limits":[]},
     {"schema":1,"vendor":"woyo","account":"measured","names":["gateway-account"],"status":"ok","limits":[],
      "usage":{"source":"gateway-log","as_of":"2026-10-14T03:35:00Z","collected_at":"2026-10-14T03:35:00Z","periods":[
        {"window_minutes":10080,"starts_at":"2026-10-07T03:35:00Z","ends_at":"2026-10-14T03:35:00Z","input_tokens":0,"output_tokens":0,"total_tokens":2936,"cache_read_tokens":0,"cache_creation_tokens":0,"coverage":"partial","reasons":[]}
      ]}}]
    """.utf8))
    let byID = Dictionary(uniqueKeysWithValues: readings.map { ("\($0.vendor)/\($0.account ?? "")", $0) })
    let failure = try #require(PopoverContent.failure(problem: "unlimited read failed", readings: byID, selected: "openai/quota"))

    #expect(failure.reading?.vendor == "openai")
    #expect(failure.usage == nil)
    #expect(try #require(PopoverContent.failure(problem: "unlimited read failed", readings: byID, selected: "woyo/measured")).reading?.vendor == "woyo")
    #expect(PopoverContent.failure(problem: "unlimited read failed", readings: byID, selected: nil)?.reading == nil)
}

@MainActor
@Test func settingsUsageUsesPrimaryHealthRatherThanAlternateWarning() throws {
    let tile = Tile(id: "openai/test", label: "CDX", value: .percent(27), dimmed: false,
                    health: .underUsed,
                    alternate: .init(role: "session", value: .percent(99), health: .red))
    let image = try usageImage(tile)
    let greenPixels = try greenColumns(image).count
    var redPixels = 0
    for y in 0..<image.pixelsHigh {
        for x in 0..<image.pixelsWide {
            let c = try #require(image.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
            if c.redComponent > 0.7 && c.redComponent > c.greenComponent * 1.3 &&
                c.blueComponent > c.greenComponent { redPixels += 1 }
        }
    }
    #expect(greenPixels > 10)
    #expect(redPixels == 0)
}

@MainActor
private func bitmap<V: View>(_ view: V, width: CGFloat, height: CGFloat) throws -> NSBitmapImageRep {
    _ = NSApplication.shared
    let host = NSHostingView(rootView: view.environment(\.colorScheme, .dark))
    host.frame = NSRect(x: 0, y: 0, width: width, height: height)
    let window = NSWindow(contentRect: host.frame, styleMask: .borderless, backing: .buffered, defer: false)
    window.contentView = host
    host.layoutSubtreeIfNeeded()
    let image = try #require(host.bitmapImageRepForCachingDisplay(in: host.bounds))
    host.cacheDisplay(in: host.bounds, to: image)
    return image
}

private func budgetCard() -> Card {
    Card(name: "local-weekly", title: "Rolling weekly", used: 0.25, elapsed: 0,
         health: .normal, resets: nil, resetsAt: nil, momentum: nil, projected: nil,
         runsOut: nil, odds: nil, fromHistory: false)
}

@MainActor
@Test func localBudgetPercentFormattingDoesNotRequireAnIntegerSizedResult() {
    let view = CardView(card: budgetCard(), showsPace: false)
    #expect(view.percent(0.25) == "25%")
    #expect(view.percent(0.005) == "1%")
    #expect(view.percent(1e20) == "10000000000000000000000%")
}

@MainActor
@Test func aRollingBudgetCardCanOmitTheResetPaceStripe() throws {
    let card = budgetCard()
    let rolling = try bitmap(CardView(card: card, showsPace: false), width: 320, height: 100)
    let paced = try bitmap(CardView(card: card), width: 320, height: 100)
    var changed = 0
    for y in 0..<rolling.pixelsHigh {
        for x in 0..<rolling.pixelsWide {
            let a = try #require(rolling.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
            let b = try #require(paced.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
            if abs(a.redComponent - b.redComponent) > 0.05 { changed += 1 }
        }
    }
    #expect(changed > 0, "a rolling budget must not pretend to have a reset-window pace")
    print("Rolling budget pace-stripe pixel difference: \(changed)")
    try save(rolling, named: "rolling-budget-card.png")
}

@MainActor
@Test func localPacingRowsRenderWithoutClipping() throws {
    let usage = try #require(try Reading.decode(Data("""
    [{"schema": 1, "vendor": "woyo", "status": "ok", "limits": [],
      "usage": {"source": "gateway-log", "as_of": "2026-09-24T06:00:00+00:00", "collected_at": "2026-09-24T05:59:00+00:00", "periods": [
        {"window_minutes": 10080, "starts_at": "2026-09-17T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 12000, "output_tokens": 500, "total_tokens": 12500, "cache_read_tokens": 0, "cache_creation_tokens": 0, "coverage": "partial", "reasons": ["horizon-edge"], "local_budget": {"status": "ok", "token_limit": 10000, "profiles": ["gateway"]}},
        {"window_minutes": 43200, "starts_at": "2026-08-25T06:00:00+00:00", "ends_at": "2026-09-24T06:00:00+00:00", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "coverage": "observed", "reasons": [], "local_budget": {"status": "invalid", "token_limit": null, "profiles": ["gateway"]}}
      ]}}]
    """.utf8))[0].usage)
    let image = try bitmap(LoggedUsageView(usage: usage, now: Date(timeIntervalSince1970: 1_790_229_600)),
                           width: 560, height: 250)
    let ink = try inkBounds(image)

    #expect(ink.minX > 0 && ink.maxX < CGFloat(image.pixelsWide - 1), "local pacing horizontal ink clipping")
    #expect(ink.minY > 0 && ink.maxY < CGFloat(image.pixelsHigh - 1), "local pacing vertical ink clipping")
    print("Local pacing detail ink extent: \(ink)")
}

private func inkBounds(_ image: NSBitmapImageRep, in rect: NSRect? = nil) throws -> NSRect {
    let area = (rect ?? NSRect(x: 0, y: 0, width: image.pixelsWide, height: image.pixelsHigh)).integral
    var minX = Int(area.maxX), minY = Int(area.maxY), maxX = -1, maxY = -1
    for y in Int(area.minY)..<Int(area.maxY) {
        for x in Int(area.minX)..<Int(area.maxX) {
            let color = try #require(image.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB))
            if max(color.redComponent, color.greenComponent, color.blueComponent) > 0.45 {
                minX = min(minX, x); maxX = max(maxX, x)
                minY = min(minY, y); maxY = max(maxY, y)
            }
        }
    }
    guard maxX >= minX, maxY >= minY else { throw CocoaError(.fileReadNoSuchFile) }
    return NSRect(x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1)
}

private struct BaselineMarker<Content: View>: View {
    let content: Content

    init(@ViewBuilder content: () -> Content) { self.content = content() }

    var body: some View {
        ZStack(alignment: Alignment(horizontal: .trailing, vertical: .firstTextBaseline)) {
            content
            Circle().fill(.red).frame(width: 2, height: 2)
                .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] }
        }
    }
}

private struct BaselineProbe: View {
    let tokenOffset: CGFloat

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            BaselineMarker {
                Text("WY").font(.system(.body, design: .monospaced))
            }
            BaselineMarker {
                UsageFigure(value: .tokens(12_345_678, windowMinutes: 10_080), health: .normal)
                    .font(.system(.body, design: .rounded).weight(.semibold))
            }
            .offset(y: tokenOffset)
        }
    }
}

private func redRowSpan(_ image: NSBitmapImageRep) throws -> Int {
    let rows = Set((0..<image.pixelsHigh).flatMap { y in
        (0..<image.pixelsWide).compactMap { x -> Int? in
            let color = image.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB)
            guard let color, color.redComponent > 0.7, color.redComponent > color.greenComponent * 1.5,
                  color.redComponent > color.blueComponent * 1.5 else { return nil }
            return y
        }
    })
    return (try #require(rows.max())) - (try #require(rows.min()))
}

private func save(_ image: NSBitmapImageRep, named name: String) throws {
    let root = URL(filePath: #filePath)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .appending(path: ".superpowers/sdd/plan/task-3-artifacts")
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    try #require(image.representation(using: .png, properties: [:])).write(to: root.appending(path: name))
}

@MainActor
@Test func measuredUsageRendersWithoutClippingInStripSettingsAndPopover() async throws {
    let cli = try UsageCLI()
    defer { cli.remove() }
    let now = Date()
    try cli.succeedMixedMeasured(takenAt: now, total: 12_345_678)
    let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { now })
    model.refresh()
    try await waitUntil { model.accounts.count == 2 }
    model.hoverExpanded = true
    let tile = try #require(model.accounts.first { $0.vendor == "woyo" })
    let measured = try #require(model.readings[tile.id]?.usage)
    let week = try #require(measured.period(windowMinutes: 10_080))
    let month = try #require(measured.period(windowMinutes: 43_200))
    #expect(abs(week.startsAt.timeIntervalSince(measured.asOf.addingTimeInterval(-7 * 86_400))) < 1)
    #expect(abs(month.startsAt.timeIntervalSince(measured.asOf.addingTimeInterval(-30 * 86_400))) < 1)
    let displayed = model.displayedTiles
    #expect(displayed.contains { if case .percent = $0.value { true } else { false } })
    #expect(displayed.contains { if case .tokens = $0.value { true } else { false } })
    let layout = StripLayout(tiles: displayed, spacing: Double(StripView.spacing), padding: Double(StripView.padding))
    let lastDisplayed = try #require(displayed.last)
    let stripWidth = CGFloat(layout.rect(of: lastDisplayed).maxX) + StripView.padding

    let strip = try bitmap(StripView(model: model), width: stripWidth, height: 22)
    let tokenTile = try bitmap(TileView(tile: tile, focused: false, alternating: false),
                               width: TileView.width(of: tile) + 4, height: 22)
    let fullValue = try bitmap(UsageFigure(value: .tokens(12_345_678, windowMinutes: 10_080), health: .normal)
        .font(TileView.valueFont).monospacedDigit().fixedSize(), width: 200, height: 22)
    let settings = try bitmap(SettingsView(model: model), width: 600, height: 680)
    model.selected = tile.id
    let normalPopover = try bitmap(PopoverView(model: model), width: 320, height: 560)
    try cli.unsupportedSchema()
    model.refresh()
    try await waitUntil { model.tiles == [.broken] && model.problem != nil }
    #expect(model.readings[tile.id]?.usage?.period(windowMinutes: 10_080)?.totalTokens == 12_345_678)
    let failedPopover = try bitmap(PopoverView(model: model), width: 320, height: 560)
    let header = try usageImage(Tile(id: "woyo/synthetic", label: "WY",
                                     value: .tokens(12_345_678, windowMinutes: 10_080), dimmed: false))
    let baseline = try bitmap(BaselineProbe(tokenOffset: 0).padding(10), width: 280, height: 50)
    let shiftedBaseline = try bitmap(BaselineProbe(tokenOffset: 4).padding(10), width: 280, height: 50)
    try save(strip, named: "strip-mixed.png")
    try save(settings, named: "settings.png")
    try save(normalPopover, named: "popover-normal.png")
    try save(failedPopover, named: "popover-failed.png")

    let stripInk = try inkBounds(strip)
    let tokenInk = try inkBounds(tokenTile)
    let fullValueInk = try inkBounds(fullValue)
    let settingsInk = try inkBounds(settings)
    let normalPopoverInk = try inkBounds(normalPopover)
    let failedPopoverInk = try inkBounds(failedPopover)
    let headerScale = CGFloat(header.pixelsWide) / 560
    let aliasRect = NSRect(x: 40 * headerScale, y: 0, width: 55 * headerScale, height: CGFloat(header.pixelsHigh))
    let valueRect = NSRect(x: 95 * headerScale, y: 0, width: 90 * headerScale, height: CGFloat(header.pixelsHigh))
    let aliasInk = try inkBounds(header, in: aliasRect)
    let valueInk = try inkBounds(header, in: valueRect)
    let baselineSpan = try redRowSpan(baseline)
    let shiftedBaselineSpan = try redRowSpan(shiftedBaseline)
    for (name, bounds, image) in [("mixed strip", stripInk, strip), ("settings", settingsInk, settings),
                                  ("normal popover", normalPopoverInk, normalPopover),
                                  ("failed popover", failedPopoverInk, failedPopover)] {
        #expect(bounds.minX > 0 && bounds.maxX <= CGFloat(image.pixelsWide - 1), "\(name) horizontal ink clipping")
        #expect(bounds.minY > 0 && bounds.maxY <= CGFloat(image.pixelsHigh - 1), "\(name) vertical ink clipping")
    }
    print("Token ink: tile=\(tokenInk), reference=\(fullValueInk), imageWidth=\(tokenTile.pixelsWide)")
    #expect(tokenInk.minX > 1 && tokenInk.maxX <= CGFloat(tokenTile.pixelsWide - 1), "token ink edge clearance")
    // Fractional raster origins can change the measured glyph span by a pixel without clipping.
    #expect(tokenInk.width + 1 >= fullValueInk.width, "the strip must hold the complete compact token figure")
    #expect(baselineSpan <= 3, "first-baseline markers share one rendered row")
    #expect(shiftedBaselineSpan >= 5, "a four-pixel token offset must separate rendered baseline markers")
    print("Measured ink extents: mixed-strip=\(stripInk), settings=\(settingsInk), normal-popover=\(normalPopoverInk), failed-popover=\(failedPopoverInk), header-alias=\(aliasInk), header-value=\(valueInk), baseline-spans=\(baselineSpan),\(shiftedBaselineSpan)")
}
