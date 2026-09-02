import SwiftUI

/// The four screens a fresh install walks through, in order, before handing
/// off to the already-built "Your notes" window.
enum OnboardingStep: Int, CaseIterable {
    case welcome, permissions, talk, listening
}

/// One row of `notron permissions --json` — field names already match, so
/// the default decoder needs no key strategy (mirrors `LibraryNote` doing
/// the opposite when the core *does* speak snake_case).
struct PermissionCheck: Codable, Equatable, Identifiable {
    var id: String { app }
    let app: String
    let ok: Bool
    let detail: String
    let fix: String
}

/// Mirrors `LibraryModel`'s `Task.detached` + `Core.run` pattern exactly —
/// every core call runs off the main thread so a Notes hang (see CLAUDE.md:
/// an unapproved automation hangs, it doesn't fail) never freezes the window.
@MainActor
final class OnboardingModel: ObservableObject {
    @Published var step: OnboardingStep = .welcome
    @Published var checks: [PermissionCheck] = []
    @Published var checksLoading = false
    @Published var listening = false
    @Published var startingListener = false

    static let file = Core.home.appendingPathComponent(".notron/onboarding.json")

    static var done: Bool { FileManager.default.fileExists(atPath: file.path) }

    func markDone() {
        let payload = ["completed_at": ISO8601DateFormatter().string(from: Date())]
        guard let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
        try? FileManager.default.createDirectory(at: Self.file.deletingLastPathComponent(),
                                                   withIntermediateDirectories: true)
        try? data.write(to: Self.file, options: .atomic)
    }

    // ------------------------------------------------------------ permissions

    func loadChecks() {
        checksLoading = true
        Task.detached { [weak self] in
            let checks = await Self.readChecks()
            await MainActor.run {
                guard let self else { return }
                self.checksLoading = false
                if let checks { self.checks = checks }
            }
        }
    }

    /// For Notes, no separate "request access" call exists — the probe itself
    /// is what surfaces the OS prompt, and an unapproved app hangs on it
    /// rather than failing (CLAUDE.md). Reminders/Calendar's EventKit prompt
    /// fires from the same probe. So "Allow" just re-runs the check off-thread
    /// and lets that hang resolve into granted or still-pending, polling for
    /// up to 8s — `permissions.PROBE_TIMEOUT` bounds any one call, but the OS
    /// dialog itself is paced by the user, not by us.
    func allow(_ app: String) {
        Task.detached { [weak self] in
            for _ in 0..<8 {
                if let checks = await Self.readChecks() {
                    await MainActor.run { self?.checks = checks }
                    if checks.first(where: { $0.app == app })?.ok == true { return }
                }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
    }

    private static func readChecks() async -> [PermissionCheck]? {
        guard let json = try? Core.run(["permissions", "--json"]) else { return nil }
        return try? JSONDecoder().decode([PermissionCheck].self, from: Data(json.utf8))
    }

    static let panes: [String: String] = [
        "Notes": "com.apple.preference.security?Privacy_Automation",
        "Calendar": "com.apple.preference.security?Privacy_Calendars",
        "Reminders": "com.apple.preference.security?Privacy_Reminders",
    ]

    func openSystemSettings(for app: String) {
        guard let pane = Self.panes[app],
              let url = URL(string: "x-apple.systempreferences:\(pane)") else { return }
        NSWorkspace.shared.open(url)
    }

    // ------------------------------------------------------------- listening

    func loadListening() {
        Task.detached { [weak self] in
            let running = await Self.readListening()
            await MainActor.run { self?.listening = running }
        }
    }

    /// Runs `listen --install` (launchd-backed, survives reboot), then polls
    /// `--status` until it flips true — typically well under a second.
    func startListening() {
        startingListener = true
        Task.detached { [weak self] in
            _ = try? Core.run(["listen", "--install"])
            for _ in 0..<8 {
                if await Self.readListening() {
                    await MainActor.run { self?.listening = true; self?.startingListener = false }
                    return
                }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
            await MainActor.run { self?.startingListener = false }
        }
    }

    private static func readListening() async -> Bool {
        guard let json = try? Core.run(["listen", "--status"]),
              let obj = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Bool]
        else { return false }
        return obj["running"] ?? false
    }
}
