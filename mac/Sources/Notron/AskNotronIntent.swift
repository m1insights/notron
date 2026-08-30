import AppIntents
import Foundation

/// The Siri-facing action: "Hey Siri, ask Notron <anything>".
/// Runs the existing Python agent as a subprocess and speaks its answer back.
struct AskNotronIntent: AppIntent {
    static var title: LocalizedStringResource = "Ask Notron"
    static var description = IntentDescription("Ask your personal Notes agent a question, out loud.")

    @Parameter(title: "Request")
    var request: String

    static var parameterSummary: some ParameterSummary {
        Summary("Ask Notron \(\.$request)")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let answer = try Self.runNotron(request)
        return .result(dialog: IntentDialog(stringLiteral: answer))
    }

    /// Shells out to the bundled Python agent, exactly like the CLI's `--quiet` mode.
    /// NOTE: path is hardcoded to the dev machine's venv for now. Shipping this in a
    /// DMG to other users requires bundling a portable Python runtime inside the .app
    /// (see mac/README.md) — this is the placeholder that makes local Siri testing work today.
    private static func runNotron(_ request: String) throws -> String {
        let pythonPath = ProcessInfo.processInfo.environment["NOTRON_PYTHON"]
            ?? "/Users/m1labs/Dev/apps/juno/.venv/bin/python"

        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonPath)
        process.arguments = ["-m", "notron", "ask", "--quiet", request]

        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe

        try process.run()
        process.waitUntilExit()

        let outData = outPipe.fileHandleForReading.readDataToEndOfFile()
        let text = String(data: outData, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

        if text.isEmpty {
            let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
            let errText = String(data: errData, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return errText.isEmpty
                ? "I don't have anything to say to that."
                : "Notron hit a problem: \(errText)"
        }
        return text
    }
}

/// Registers the Siri phrases system-wide the moment the app is installed and run once.
struct NotronShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: AskNotronIntent(),
            phrases: [
                "Ask \(.applicationName) \(\.$request)",
                "\(.applicationName), \(\.$request)"
            ],
            shortTitle: "Ask Notron",
            systemImageName: "brain.head.profile"
        )
    }
}
