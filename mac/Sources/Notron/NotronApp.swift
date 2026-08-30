import SwiftUI
import AppIntents

/// Reads `.notron/mood.json`, which `care.py` rewrites every morning (or on a manual
/// `notron care`), so the menu bar glyph shows her real state without opening Notes.
/// Same dev-machine placeholder as `AskNotronIntent`'s Python path — see its comment.
@MainActor
final class MoodWatcher: ObservableObject {
    @Published var emoji = "🤖"
    @Published var label = "Notron"

    private static let path: URL = {
        let home = ProcessInfo.processInfo.environment["NOTRON_HOME"]
            ?? "/Users/m1labs/Dev/apps/juno"
        return URL(fileURLWithPath: home).appendingPathComponent(".notron/mood.json")
    }()

    private var timer: Timer?

    init() {
        reload()
        // care.py only rewrites this once a day, but polling is cheap and it also
        // catches a manual `notron care` run without any IPC between the two sides.
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.reload() }
        }
    }

    private func reload() {
        guard let data = try? Data(contentsOf: Self.path),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: String]
        else { return }
        emoji = json["emoji"] ?? "🤖"
        label = json["label"] ?? "Notron"
    }
}

@main
struct NotronApp: App {
    @StateObject private var mood = MoodWatcher()

    init() {
        // Tells the system what phrases exist before the user ever opens the app.
        NotronShortcuts.updateAppShortcutParameters()
    }

    var body: some Scene {
        MenuBarExtra {
            Text(mood.label)
            Text("Say \u{201C}Hey Siri, ask Notron\u{2026}\u{201D} anytime.")
            Divider()
            Button("Quit Notron") { NSApplication.shared.terminate(nil) }
        } label: {
            Text(mood.emoji)
        }
        .menuBarExtraStyle(.menu)
    }
}
