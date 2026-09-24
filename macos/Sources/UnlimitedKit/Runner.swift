import Foundation

/// Runs the `unlimited` CLI. The app never reads a credential or calls a vendor; this is its
/// only source.
public struct Runner: Sendable {
    public static let minimumVersion = [0, 0, 23]  // roles even for limits an older install cached

    public let binary: URL

    public init(binary: URL) { self.binary = binary }

    /// A login-item launch gets no shell `PATH`, so look where installers put it.
    public static func locate(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> Runner? {
        let candidates = [home.appending(path: ".local/bin/unlimited"),
                          URL(filePath: "/opt/homebrew/bin/unlimited"), URL(filePath: "/usr/local/bin/unlimited")]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0.path) }.map(Runner.init)
    }

    /// A cold read asks every vendor in turn; the timeout leaves room for a slow one.
    public func read(maxAge: Int? = nil, timeout: TimeInterval = 45) throws -> [Reading] {
        try Reading.decode(run(["read", "--json"] + (maxAge.map { ["--max-age", String($0)] } ?? []), timeout: timeout))
    }

    /// Whether this install emits what the strip needs.
    public func isRecentEnough() -> Bool {
        guard let out = try? run(["--version"], timeout: 10),
              let v = Runner.version(String(decoding: out, as: UTF8.self)) else { return false }
        return v.lexicographicallyPrecedes(Runner.minimumVersion) == false
    }

    static func version(_ s: String) -> [Int]? {
        let parts = s.split(separator: " ").last?.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: ".")
        let nums = parts?.compactMap { Int($0) }
        return nums?.count == 3 ? nums : nil
    }

    func run(_ args: [String], timeout: TimeInterval) throws -> Data {
        let p = Process()
        p.executableURL = binary
        p.arguments = args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        try p.run()
        let killer = DispatchWorkItem { p.terminate() }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        killer.cancel()
        guard p.terminationStatus == 0 else { throw RunError.exit(p.terminationStatus) }
        return data
    }

    public enum RunError: Error { case exit(Int32) }
}
