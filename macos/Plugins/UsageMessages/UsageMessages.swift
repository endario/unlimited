import Foundation
import PackagePlugin

@main
struct UsageMessages: BuildToolPlugin {
    func createBuildCommands(context: PluginContext, target: Target) throws -> [Command] {
        let source = context.package.directoryURL.appending(path: "../src/unlimited/usage_errors.json").standardizedFileURL
        let output = context.pluginWorkDirectoryURL.appending(path: "Resources")
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        // Native SwiftPM preserves relative symlinks, which break after copying into its resource bundle.
        return [.prebuildCommand(displayName: "Copy shared usage messages",
                                 executable: URL(filePath: "/bin/cp"),
                                 arguments: [source.path, output.appending(path: "usage_errors.json").path],
                                 outputFilesDirectory: output)]
    }
}
