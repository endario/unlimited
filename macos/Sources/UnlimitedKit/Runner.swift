import Foundation

/// Runs the `unlimited` CLI. The app never reads a credential or calls a vendor; this is its
/// only source.
public struct Runner: Sendable {
    public static let minimumVersion = [0, 0, 23]  // roles even for limits an older install cached; 0.0.24 adds today's pace

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
        try readRaw(maxAge: maxAge, timeout: timeout).1
    }

    /// The readings and the exact JSON they came in.
    public func readRaw(maxAge: Int? = nil, timeout: TimeInterval = 45) throws -> (Data, [Reading]) {
        let data = try run(["read", "--json"] + (maxAge.map { ["--max-age", String($0)] } ?? []), timeout: timeout)
        return (data, try Reading.decode(data))
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

    public func run(_ args: [String], timeout: TimeInterval, stdin: Data? = nil) throws -> Data {
        try runCaptured(args, timeout: timeout, stdin: stdin).0
    }

    /// The one process-plumbing copy: run the CLI, kill it at the timeout, keep its stderr for
    /// the error a caller may want to surface. Stderr goes to a file, not a pipe — a chatty
    /// child cannot then stall the stdout read until the timeout.
    func runCaptured(_ args: [String], timeout: TimeInterval, stdin: Data? = nil) throws -> (Data, String) {
        let errPath = FileManager.default.temporaryDirectory.appending(path: "unlimited-\(UUID().uuidString).err")
        FileManager.default.createFile(atPath: errPath.path, contents: nil,
                                       attributes: [.posixPermissions: 0o600])
        // Input goes through a private file, not a pipe: a child that exits without reading it
        // cannot then break the writer, and the payload has no pipe buffer to overflow.
        var inPath: URL?
        if let stdin {
            let path = FileManager.default.temporaryDirectory.appending(path: "unlimited-\(UUID().uuidString).in")
            FileManager.default.createFile(atPath: path.path, contents: stdin,
                                           attributes: [.posixPermissions: 0o600])
            inPath = path
        }
        let p = Process()
        p.executableURL = binary
        p.arguments = args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = try FileHandle(forWritingTo: errPath)
        if let inPath { p.standardInput = try FileHandle(forReadingFrom: inPath) }
        try p.run()
        let killer = DispatchWorkItem { p.terminate() }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        killer.cancel()
        let message = String(decoding: (try? Data(contentsOf: errPath)) ?? Data(), as: UTF8.self)
        try? FileManager.default.removeItem(at: errPath)
        if let inPath { try? FileManager.default.removeItem(at: inPath) }
        guard p.terminationStatus == 0 else { throw RunError.exit(p.terminationStatus, stderr: message) }
        return (data, message)
    }

    public enum RunError: Error {
        case exit(Int32, stderr: String)
    }
}
