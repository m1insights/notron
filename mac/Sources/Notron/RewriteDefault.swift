import SwiftUI

/// The global default for new notes: when `organizer` offers to tidy a note
/// up in place, should she ask first, always do it, or never? Decision 3 of
/// docs/plans/2026-09-03-rewrite-permission-and-undo-design.md — the per-note
/// real-example ask (organizer's own in-Notes proposal) always wins on a note
/// that's already been touched; this only decides where a *fresh* note starts.
enum RewriteDefault: String, CaseIterable {
    case ask, always, never

    var label: String {
        switch self {
        case .ask: return "Ask each time"
        case .always: return "Always clean it up"
        case .never: return "Never — add below instead"
        }
    }
}

/// Reads `.notron/rewrite.json` directly — the same direct-file-read pattern
/// `LibraryCounts`/`MoodWatcher` already use in `NotronApp.swift` for local
/// state the core owns. Writes go back out through `Core.run`, like every
/// other write in this app: `notron rewrite --default …`.
enum RewriteDefaultState {
    static let file = Core.home.appendingPathComponent(".notron/rewrite.json")

    /// True until the user has chosen the *global* default once, ever —
    /// covers a brand-new install (file doesn't exist yet) and an upgrade
    /// from a build that predates this screen (file exists, `default_chosen_at`
    /// doesn't). Deliberately its own field, not `chosen_at` — that one is
    /// stamped by a per-note `@notron yes` (`rewrite.allow`, in Notes or the
    /// CLI, no Mac app involved), a different question from "has the user
    /// ever picked the global default." Sharing a field would mean this
    /// sheet never shows for a user who'd already answered one note's offer.
    static var needsChoice: Bool {
        guard let data = try? Data(contentsOf: file),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let chosenAt = json["default_chosen_at"] as? String
        else { return true }
        return chosenAt.isEmpty
    }

    static func choose(_ value: RewriteDefault) {
        Task.detached { _ = try? Core.run(["rewrite", "--default", value.rawValue]) }
    }
}

/// A fabricated before/after — decision 2 in the design doc is explicit that
/// the *real* ask always happens later, on the real note; onboarding runs
/// before any note has been touched, so this can only ever be a sample.
struct RewriteDefaultSheet: View {
    let finish: () -> Void
    @State private var choice: RewriteDefault = .ask

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Space.s5) {
            VStack(alignment: .leading, spacing: DS.Space.s1) {
                Text("Keep notes tidy automatically?").font(DS.Font.headline).foregroundStyle(DS.Color.text)
                Text("When you ask her to clean up a note, should she ask first, or just do it?")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }

            HStack(alignment: .top, spacing: DS.Space.s4) {
                BeforeAfterCard(label: "Before", lines: [
                    "parking garage info", "west side gar $18/day",
                    "level 3 closest to elevator", "enter on 5th ave",
                ])
                BeforeAfterCard(label: "After", lines: [
                    "Parking", "• West garage: $18/day",
                    "• Level 3 — closest to elevator", "• Enter on 5th Ave",
                ])
            }

            Segmented(values: RewriteDefault.allCases, selection: choice,
                      label: { $0.label }, choose: { choice = $0 })

            Text("Any single note can still say yes or no on its own — this only sets where a fresh note starts.")
                .font(DS.Font.label).foregroundStyle(DS.Color.textFaint)

            Spacer()
            HStack {
                Button("Decide later", action: finish)
                    .buttonStyle(.plain).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                Spacer()
                Button("Continue") {
                    RewriteDefaultState.choose(choice)
                    finish()
                }
                .buttonStyle(.plain)
                .font(DS.Font.body)
                .foregroundStyle(DS.Color.bg)
                .padding(.horizontal, DS.Space.s5).padding(.vertical, DS.Space.s3)
                .background(DS.Color.accent)
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.md))
            }
        }
        .padding(DS.Space.s6)
        .frame(minWidth: 520, idealWidth: 560, minHeight: 420, idealHeight: 440)
        .background(DS.Color.bg)
    }
}

private struct BeforeAfterCard: View {
    let label: String
    let lines: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Space.s2) {
            Text(label).font(DS.Font.label).foregroundStyle(DS.Color.textFaint)
            VStack(alignment: .leading, spacing: DS.Space.s1) {
                ForEach(lines, id: \.self) { line in
                    Text(line).font(DS.Font.caption).foregroundStyle(DS.Color.text)
                }
            }
        }
        .padding(DS.Space.s4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Color.surface)
        .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairline))
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
    }
}
