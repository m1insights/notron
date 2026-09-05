import SwiftUI
import AppIntents

/// Reads managed Application Support `mood.json`, which `care.py` rewrites every morning (or on a manual
/// `notron care`), so the menu bar glyph shows her real state without opening Notes.
/// Matches the Python managed-storage root.
@MainActor
final class MoodWatcher: ObservableObject {
    @Published var emoji = "🤖"
    @Published var label = "Notron"

    private static let path: URL = {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/com.m1labs.notron/mood.json")
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

/// "12 homes · 9 ignored" in the menu, read straight from .notron/library.json
/// so the menu bar tells the truth without a round trip to the core.
@MainActor
final class LibraryCounts: ObservableObject {
    @Published var line: String? = nil
    private var timer: Timer?

    init() {
        reload()
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.reload() }
        }
    }

    private func reload() {
        guard let data = try? Data(contentsOf: LibraryModel.file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { line = nil; return }
        let homes = (json["homes"] as? [String])?.count ?? 0
        let ignored = (json["ignore"] as? [String])?.count ?? 0
        line = "\(homes) homes · \(ignored) ignored"
    }
}

@main
struct NotronApp: App {
    @StateObject private var mood = MoodWatcher()
    @StateObject private var library = LibraryCounts()

    init() {
        // Tells the system what phrases exist before the user ever opens the app.
        NotronShortcuts.updateAppShortcutParameters()
    }

    var body: some Scene {
        MenuBarExtra {
            Text(mood.label)
            if let line = library.line {
                Text(line)
            }
            Text("Say \u{201C}Hey Siri, ask Notron\u{2026}\u{201D} anytime.")
            Divider()
            OpenLibraryButton(title: LibraryModel.exists ? "Your notes\u{2026}" : "Set up your notes\u{2026}")
            Divider()
            Button("Quit Notron") { NSApplication.shared.terminate(nil) }
        } label: {
            MenuBarLabel(emoji: mood.emoji)
        }
        .menuBarExtraStyle(.menu)

        Window("Your notes", id: "library") {
            YourNotesView()
        }
        .windowResizability(.contentMinSize)

        Window("Welcome", id: "onboarding") {
            OnboardingView()
        }
        .windowResizability(.contentMinSize)
    }
}

/// The menu bar glyph — and, because it is the one view that exists from
/// launch, the place a first run opens the right window on its own:
/// onboarding for a Mac that has never finished it, else today's "Your
/// notes" first-run check (covers a user with an old `.notron/onboarding.json`
/// from a previous build who never finished "Your notes" — don't force them
/// back through Welcome), else nothing — menu bar only, as today.
private struct MenuBarLabel: View {
    let emoji: String
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Text(emoji)
            .onAppear {
                if !OnboardingModel.done {
                    NSApp.activate(ignoringOtherApps: true)
                    openWindow(id: "onboarding")
                } else if !LibraryModel.exists {
                    NSApp.activate(ignoringOtherApps: true)
                    openWindow(id: "library")
                }
            }
    }
}

private struct OpenLibraryButton: View {
    let title: String
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button(title) {
            NSApp.activate(ignoringOtherApps: true)
            openWindow(id: "library")
        }
    }
}
