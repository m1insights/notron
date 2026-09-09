import Foundation
import NotronCore

/// The one way the app talks to the Python core: a subprocess, exactly like the
/// CLI. Dev-machine paths for now — shipping a DMG means bundling a Python
/// runtime inside the .app (see mac/README.md); these two environment variables
/// are the seam that will point at it.
enum Core {
    // P06 owns signature/runtime validation. Never enable through an env flag.
    static let protectedManagedStartupValidated = false
    @MainActor static var managedIPC:ManagedIPCSession?

    static let home: URL = {
        let path = ProcessInfo.processInfo.environment["NOTRON_HOME"] ?? "/Users/m1labs/Dev/apps/juno"
        return URL(fileURLWithPath: path)
    }()

    static let python: String =
        ProcessInfo.processInfo.environment["NOTRON_PYTHON"] ?? "/Users/m1labs/Dev/apps/juno/.venv/bin/python"

    struct Failure: Error, CustomStringConvertible {
        let description: String
    }

    /// Runs `python -m notron <args>` and returns what it printed. If it printed
    /// nothing, whatever it wrote to stderr becomes the error.
    static func run(_ args: [String], input: Data? = nil) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-m", "notron"] + args
        process.currentDirectoryURL = home

        let out = Pipe()
        let err = Pipe()
        process.standardOutput = out
        process.standardError = err

        let stdin = Pipe()
        if input != nil { process.standardInput = stdin }
        var managedInput:FileHandle?
        if protectedManagedStartupValidated,input == nil {
            guard !Thread.isMainThread else {throw Failure(description:"Managed work must run off the UI thread.")}
            managedInput=try DispatchQueue.main.sync {
                try MainActor.assumeIsolated {
                    try managedIPC?.makeChannel(onStop:{if process.isRunning {process.terminate()}})
                }
            }
            if let managedInput {
                process.standardInput=managedInput
                var environment=ProcessInfo.processInfo.environment
                environment["NOTRON_MANAGED_SESSION_FD"]="0"
                process.environment=environment
            }
        }
        defer {try? managedInput?.close()}
        try process.run()
        if let input {
            stdin.fileHandleForWriting.write(input)
            try stdin.fileHandleForWriting.close()
        }
        process.waitUntilExit()

        let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if text.isEmpty || process.terminationStatus != 0 {
            let problem = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            throw Failure(description: problem.isEmpty ? "Notron said nothing." : problem)
        }
        return text
    }
}
