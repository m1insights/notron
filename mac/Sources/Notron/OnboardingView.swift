import SwiftUI

/// Welcome → Permissions → How to talk to her → Start listening → handoff to
/// the existing "Your notes" window. Chrome only — `OnboardingModel` does
/// every Core.run bridge, exactly like `YourNotesView`/`LibraryModel`'s split.
struct OnboardingView: View {
    @StateObject private var model = OnboardingModel()
    @Environment(\.openWindow) private var openWindow
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: DS.Space.s5) {
            StepDots(step: model.step)
            Group {
                switch model.step {
                case .welcome:
                    WelcomeStep { model.step = .permissions }
                case .permissions:
                    PermissionsStep(model: model) { model.step = .talk }
                case .talk:
                    TalkStep { model.step = .listening }
                case .listening:
                    ListeningStep(model: model, finish: finish)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .padding(DS.Space.s6)
        .frame(minWidth: 720, idealWidth: 720, minHeight: 520, idealHeight: 520)
        .background(DS.Color.bg)
    }

    /// The point the whole sequence is marked complete — also the handoff to
    /// the already-built "Your notes" window, not a rebuild of it.
    private func finish() {
        model.markDone()
        NSApp.activate(ignoringOtherApps: true)
        openWindow(id: "library")
        dismiss()
    }
}

// ---------------------------------------------------------------- step dots

/// Row of small dots, one per step — never a numbered "Step 2 of 4" label.
private struct StepDots: View {
    let step: OnboardingStep

    var body: some View {
        HStack(spacing: DS.Space.s2) {
            ForEach(OnboardingStep.allCases, id: \.self) { s in
                Circle()
                    .fill(s == step ? DS.Color.accent
                          : s.rawValue < step.rawValue ? DS.Color.textFaint : Color.clear)
                    .overlay(Circle().stroke(DS.Color.hairline, lineWidth: s.rawValue > step.rawValue ? 1 : 0))
                    .frame(width: 6, height: 6)
            }
        }
    }
}

// ------------------------------------------------------------------ welcome

/// Reuses the **Empty/first-run state** spec verbatim: icon tile, headline,
/// one body line, single button, no skip.
private struct WelcomeStep: View {
    let advance: () -> Void

    var body: some View {
        VStack(spacing: DS.Space.s5) {
            Spacer()
            Text("🤖")
                .font(.system(size: 40))
                .frame(width: 72, height: 72)
                .background(DS.Color.accent.opacity(0.15))
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
            VStack(spacing: DS.Space.s2) {
                Text("Meet Notron").font(DS.Font.headline).foregroundStyle(DS.Color.text)
                Text("She lives in your Notes, thinks on Nemotron, and writes back what you ask.")
                    .font(DS.Font.body).foregroundStyle(DS.Color.textDim)
                    .multilineTextAlignment(.center)
            }
            Spacer()
            PrimaryButton("Get started", action: advance)
        }
    }
}

// -------------------------------------------------------------- permissions

private struct PermissionCopy {
    let app: String
    let headline: String
    let why: String

    static let all = [
        PermissionCopy(app: "Notes", headline: "Notron reads and writes in Notes",
                        why: "This is where you talk to her."),
        PermissionCopy(app: "Reminders", headline: "Notron can see your reminders",
                        why: "So she knows what's still outstanding."),
        PermissionCopy(app: "Calendar", headline: "Notron can see your calendar",
                        why: "So she knows what's already on your day."),
    ]
}

/// Unchanged from `02-screens.md`'s original spec — Notes → Reminders →
/// Calendar cards, now wired to live `checks` instead of static copy. No
/// skip: CLAUDE.md's hang-not-fail behavior makes that unsafe here.
private struct PermissionsStep: View {
    @ObservedObject var model: OnboardingModel
    let advance: () -> Void

    private var allGranted: Bool {
        !model.checks.isEmpty && PermissionCopy.all.allSatisfy { copy in
            model.checks.first { $0.app == copy.app }?.ok == true
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Space.s4) {
            VStack(alignment: .leading, spacing: DS.Space.s1) {
                Text("Give Notron access").font(DS.Font.headline).foregroundStyle(DS.Color.text)
                Text("Three permissions, one at a time — she can't read or write anywhere without them.")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }
            VStack(spacing: DS.Space.s3) {
                ForEach(PermissionCopy.all, id: \.app) { copy in
                    PermissionCard(copy: copy, check: model.checks.first { $0.app == copy.app },
                                   loading: model.checksLoading,
                                   allow: { model.allow(copy.app) },
                                   openSettings: { model.openSystemSettings(for: copy.app) })
                }
            }
            Spacer()
            HStack {
                Spacer()
                PrimaryButton("Continue", action: advance).disabled(!allGranted)
            }
        }
        .onAppear { model.loadChecks() }
    }
}

private struct PermissionCard: View {
    let copy: PermissionCopy
    let check: PermissionCheck?
    let loading: Bool
    let allow: () -> Void
    let openSettings: () -> Void

    private var granted: Bool { check?.ok == true }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Space.s3) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: DS.Space.s1) {
                    Text(copy.headline).font(DS.Font.body).foregroundStyle(DS.Color.text)
                    Text(copy.why).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                }
                Spacer()
                if granted {
                    HStack(spacing: DS.Space.s1) {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(DS.Color.success)
                        Text("Connected").font(DS.Font.caption).foregroundStyle(DS.Color.success)
                    }
                } else if loading && check == nil {
                    ProgressView().controlSize(.small)
                } else {
                    Button("Allow", action: allow)
                        .buttonStyle(.plain)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Color.bg)
                        .padding(.horizontal, DS.Space.s4).padding(.vertical, DS.Space.s2)
                        .background(DS.Color.accent)
                        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.md))
                }
            }
            if let fix = check?.fix, !granted, !fix.isEmpty {
                VStack(alignment: .leading, spacing: DS.Space.s2) {
                    Text(fix).font(DS.Font.label).foregroundStyle(DS.Color.textFaint)
                    Button("Open System Settings", action: openSettings)
                        .buttonStyle(.plain)
                        .font(DS.Font.caption).foregroundStyle(DS.Color.accent)
                }
            }
        }
        .padding(DS.Space.s4)
        .background(DS.Color.surface)
        .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairline))
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
        .animation(DS.Motion.standard, value: granted)
    }
}

// --------------------------------------------------------- how to talk to her

/// The single highest-leverage screen in the whole gap: three real examples,
/// not abstract copy, for a user who has no way to discover `#notron` on their own.
private struct TalkStep: View {
    let advance: () -> Void

    private struct Row { let glyph: String; let name: String; let example: String }
    private static let rows: [Row] = [
        Row(glyph: "📥", name: "Ask Notron", example: "Type anything in this note. She answers right below it."),
        Row(glyph: "🧠", name: "Brain Dump",
            example: "One thought per line — “took vitamin D,” “5k run 24:10.” She sorts it into the right note."),
        Row(glyph: "#", name: "notron", example: "Tag any note, anywhere in Notes, and she'll notice next time she looks."),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Space.s4) {
            Text("How to talk to her").font(DS.Font.headline).foregroundStyle(DS.Color.text)
            VStack(spacing: DS.Space.s3) {
                ForEach(Self.rows, id: \.name) { row in
                    ExampleRow(glyph: row.glyph, name: row.name, example: row.example)
                }
            }
            Text("All three live in 🤖 NOTRON — already in your Notes.")
                .font(DS.Font.caption).foregroundStyle(DS.Color.textFaint)
            Spacer()
            HStack {
                Button("Skip", action: advance)
                    .buttonStyle(.plain).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                Spacer()
                PrimaryButton("Got it", action: advance)
            }
        }
    }
}

private struct ExampleRow: View {
    let glyph: String
    let name: String
    let example: String

    var body: some View {
        HStack(alignment: .top, spacing: DS.Space.s3) {
            Text(glyph).font(DS.Font.headline).foregroundStyle(DS.Color.text)
                .frame(width: 32, alignment: .leading)
            VStack(alignment: .leading, spacing: DS.Space.s1) {
                Text(name).font(DS.Font.body).foregroundStyle(DS.Color.text)
                Text(example).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }
        }
        .padding(DS.Space.s4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.Color.surface)
        .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairline))
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
    }
}

// ------------------------------------------------------------- start listening

/// The one screen that turns Notron on — everything before it is inert
/// until this button is pressed.
private struct ListeningStep: View {
    @ObservedObject var model: OnboardingModel
    let finish: () -> Void

    var body: some View {
        VStack(spacing: DS.Space.s5) {
            Text("Turn her on").font(DS.Font.headline).foregroundStyle(DS.Color.text)
            ListeningCard(model: model)
            Spacer()
            HStack {
                if !model.listening {
                    Button("I'll do this later", action: finish)
                        .buttonStyle(.plain).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                }
                Spacer()
                PrimaryButton(model.listening ? "Set up your notes" : "Start listening") {
                    model.listening ? finish() : model.startListening()
                }
                .disabled(model.startingListener)
            }
        }
        .onAppear { model.loadListening() }
    }
}

private struct ListeningCard: View {
    @ObservedObject var model: OnboardingModel

    var body: some View {
        VStack(spacing: DS.Space.s3) {
            if model.listening {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 28)).foregroundStyle(DS.Color.success)
                Text("Notron is listening").font(DS.Font.body).foregroundStyle(DS.Color.text)
                Text("Try it — type #notron hello in any note, or say “Hey Siri, ask Notron…”")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textFaint)
                    .multilineTextAlignment(.center)
            } else {
                Image(systemName: "waveform")
                    .font(.system(size: 22)).foregroundStyle(DS.Color.accent)
                    .frame(width: 56, height: 56)
                    .background(DS.Color.accent.opacity(0.15))
                    .clipShape(RoundedRectangle(cornerRadius: DS.Radius.md))
                Text("Notron isn't listening yet").font(DS.Font.body).foregroundStyle(DS.Color.text)
                Text("Turn this on and she'll notice what you type within a few seconds, even after you restart your Mac.")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                    .multilineTextAlignment(.center)
                if model.startingListener { ProgressView().controlSize(.small) }
            }
        }
        .padding(DS.Space.s6)
        .frame(maxWidth: .infinity)
        .background(DS.Color.surface)
        .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairline))
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
        .animation(DS.Motion.standard, value: model.listening)
    }
}

// ----------------------------------------------------------------- shared

private struct PrimaryButton: View {
    let title: String
    let action: () -> Void

    init(_ title: String, action: @escaping () -> Void) {
        self.title = title
        self.action = action
    }

    var body: some View {
        Button(title, action: action)
            .buttonStyle(.plain)
            .font(DS.Font.body)
            .foregroundStyle(DS.Color.bg)
            .padding(.horizontal, DS.Space.s5).padding(.vertical, DS.Space.s3)
            .background(DS.Color.accent)
            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.md))
    }
}
