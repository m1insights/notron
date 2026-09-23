import SwiftUI

/// One hand-off task as `notron tasks --json` reports it (`handoff.Task.view`).
/// Decoded with snake_case keys so the two sides never drift.
struct BoardTask: Identifiable, Codable, Equatable {
    struct Brief: Codable, Equatable {
        var goal: String = ""
        var steps: [String] = []
        var files: [String] = []
        var doneWhen: String = ""
        var outOfScope: [String] = []
    }

    struct Review: Codable, Equatable {
        var verdict: String?
        var summary: String?
        var concerns: [String]?
        var next: String?
    }

    let id: String
    let channel: String
    let request: String
    let brief: Brief
    let digest: String
    let hand: String
    let handName: String
    let status: String
    let created: Double
    let approved: Double?
    let started: Double?
    let finished: Double?
    let branch: String
    let summary: String
    let denials: [String]
    let diffstat: String
    let changed: [String]
    let outside: [String]
    let secretInDiff: Bool
    let review: Review
    let timings: [String: Double]
    let error: String

    var goal: String { brief.goal.isEmpty ? request : brief.goal }
    var waiting: Bool { status == "proposed" }
    var active: Bool { ["proposed", "approved", "running"].contains(status) }

    /// What the row says about where it is, in words, never a colour.
    var statusWords: String {
        switch status {
        case "proposed": return "Waiting for your go"
        case "approved": return "Approved — starting"
        case "running": return "\(handName) is working"
        case "finished": return "Done — being reviewed"
        case "reported": return verdictWords
        case "failed": return "Didn't finish"
        case "cancelled": return "Cancelled"
        case "expired": return "Expired"
        default: return status
        }
    }

    var verdictWords: String {
        if !outside.isEmpty || secretInDiff { return "Needs a careful look" }
        switch review.verdict {
        case "done": return "Done"
        case "partial": return "Partly done"
        case "off_brief": return "Off brief"
        case "unsafe": return "Needs a careful look"
        case "nothing": return "No change made"
        default: return "Reported"
        }
    }
}

@MainActor
final class TaskBoardModel: ObservableObject {
    @Published var tasks: [BoardTask] = []
    @Published var selected: String?
    @Published var problem: String?
    @Published var busy = false
    private var timer: Timer?

    var current: BoardTask? { tasks.first { $0.id == selected } ?? tasks.first }

    func start() {
        load()
        // The listener moves tasks along every few seconds; the board follows.
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.load() }
        }
    }

    func stop() { timer?.invalidate() }

    func load() {
        Task.detached {
            do {
                let text = try Core.run(["tasks", "--json", "--limit", "30"])
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase
                let tasks = try decoder.decode([BoardTask].self, from: Data(text.utf8))
                await MainActor.run {
                    if tasks != self.tasks { self.tasks = tasks }
                    self.problem = nil
                }
            } catch {
                await MainActor.run { self.problem = "\(error)" }
            }
        }
    }

    /// Approves exactly the brief on screen: the digest shown is the digest sent.
    func approve(_ task: BoardTask) {
        act(["tasks", "approve", task.id, "--digest", task.digest, "--json"])
    }

    func cancel(_ task: BoardTask) {
        act(["tasks", "cancel", task.id, "--json"])
    }

    private func act(_ args: [String]) {
        busy = true
        Task.detached {
            do {
                _ = try Core.run(args)
                await MainActor.run { self.busy = false; self.problem = nil; self.load() }
            } catch {
                await MainActor.run { self.busy = false; self.problem = "\(error)" }
            }
        }
    }
}

/// The hero screen: every piece of work Nemotron briefed, what it decided and
/// how fast, the approval bound to the exact brief, and what came back.
struct TaskBoardView: View {
    @StateObject private var model = TaskBoardModel()

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Space.s4) {
            VStack(alignment: .leading, spacing: DS.Space.s1) {
                Text("Tasks").font(DS.Font.headline).foregroundStyle(DS.Color.text)
                Text("Work Nemotron briefed from your project notes. Approve here, or say \u{201C}go\u{201D} in the note.")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }
            if model.tasks.isEmpty {
                empty
            } else {
                HStack(alignment: .top, spacing: DS.Space.s4) {
                    list.frame(width: 360)
                    if let task = model.current { TaskDetail(task: task, model: model) }
                }
            }
            if let problem = model.problem {
                Text(problem).font(DS.Font.caption).foregroundStyle(DS.Color.textDim).lineLimit(2)
            }
        }
        .padding(DS.Space.s6)
        .frame(minWidth: 960, idealWidth: 1040, minHeight: 600, idealHeight: 680, alignment: .topLeading)
        .background(DS.Color.bg)
        .onAppear { model.start() }
        .onDisappear { model.stop() }
    }

    private var empty: some View {
        VStack(spacing: DS.Space.s3) {
            Text("No tasks yet").font(DS.Font.body).foregroundStyle(DS.Color.text)
            Text("Ask for a change in a project note that allows running — \u{201C}fix the checkout crash\u{201D} — and the brief appears here.")
                .font(DS.Font.caption).foregroundStyle(DS.Color.textDim).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(DS.Space.s6)
        .background(DS.Color.surface)
        .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairline))
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
    }

    private var list: some View {
        ScrollView {
            VStack(spacing: 0) {
                ForEach(model.tasks) { task in
                    let on = task.id == model.current?.id
                    VStack(alignment: .leading, spacing: DS.Space.s1) {
                        Text(task.goal).font(DS.Font.body).foregroundStyle(DS.Color.text).lineLimit(2)
                        Text("\(task.channel) · \(task.statusWords) · \(ago(task.created))")
                            .font(DS.Font.caption).foregroundStyle(DS.Color.textFaint)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(DS.Space.s3)
                    // Waiting on the user is the one row that should pull the eye:
                    // full weight and the accent edge. Everything settled dims.
                    .opacity(task.active ? 1 : 0.6)
                    .background(on ? DS.Color.surfaceAlt : DS.Color.surface)
                    .overlay(alignment: .leading) {
                        if task.waiting { Rectangle().fill(DS.Color.accent).frame(width: 3) }
                    }
                    .contentShape(Rectangle())
                    .onTapGesture { model.selected = task.id }
                    Rectangle().fill(DS.Color.hairline).frame(height: 1)
                }
            }
        }
        .background(DS.Color.surface)
        .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairline))
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
    }
}

private struct TaskDetail: View {
    let task: BoardTask
    @ObservedObject var model: TaskBoardModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DS.Space.s5) {
                section("Asked in \(task.channel)") {
                    Text(task.request).font(DS.Font.body).foregroundStyle(DS.Color.text)
                        .textSelection(.enabled)
                }
                section("Who decided what") { decisions }
                section("The brief") { brief }
                if task.waiting || task.status == "approved" || task.status == "running" { actions }
                if ["finished", "reported", "failed"].contains(task.status) {
                    section("What came back") { result }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(DS.Space.s5)
        }
        .background(DS.Color.surface)
        .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairline))
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
    }

    /// The hero beat: every decision on the way in and out is Nemotron's, and
    /// each one carries its measured time. The coding agent only has hands.
    private var decisions: some View {
        VStack(alignment: .leading, spacing: DS.Space.s2) {
            if let s = task.timings["decide"] { row("Understood the request", "Nemotron Super", seconds(s)) }
            if let s = task.timings["brief"] { row("Wrote the brief", "Nemotron Super", seconds(s)) }
            row("Approved", "You", task.approved.map { ago($0) } ?? "not yet")
            if let s = task.timings["run"] { row("Made the change", "\(task.handName) (your install)", seconds(s)) }
            else if task.status == "running" { row("Making the change", "\(task.handName) (your install)", "working…") }
            if let s = task.timings["review"] { row("Reviewed it against the brief", "Nemotron Super", seconds(s)) }
        }
    }

    private var brief: some View {
        VStack(alignment: .leading, spacing: DS.Space.s2) {
            Text(task.brief.goal).font(DS.Font.body).foregroundStyle(DS.Color.text)
            ForEach(Array(task.brief.steps.enumerated()), id: \.offset) { i, step in
                Text("\(i + 1). \(step)").font(DS.Font.caption).foregroundStyle(DS.Color.text)
            }
            if !task.brief.doneWhen.isEmpty {
                Text("Done when: \(task.brief.doneWhen)").font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }
            if !task.brief.outOfScope.isEmpty {
                Text("Not doing: \(task.brief.outOfScope.joined(separator: "; "))")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }
        }
    }

    private var actions: some View {
        VStack(alignment: .leading, spacing: DS.Space.s2) {
            HStack(spacing: DS.Space.s3) {
                if task.waiting {
                    Button { model.approve(task) } label: {
                        Text("Approve — run with \(task.handName)")
                            .font(DS.Font.body).foregroundStyle(DS.Color.bg)
                            .padding(.horizontal, DS.Space.s4).padding(.vertical, DS.Space.s3)
                            .background(DS.Color.accent)
                            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.md))
                    }
                    .buttonStyle(.plain).disabled(model.busy)
                }
                Button(task.status == "running" ? "Stop it" : "Cancel") { model.cancel(task) }
                    .buttonStyle(.plain).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                    .disabled(model.busy)
            }
            Text(task.waiting
                 ? "Approves this exact brief (\(String(task.digest.prefix(8)))). It runs in a throwaway copy, fenced off from your keys. Nothing is pushed."
                 : "Runs in a throwaway copy of the repo, fenced off from your keys. Nothing is pushed.")
                .font(DS.Font.caption).foregroundStyle(DS.Color.textFaint)
        }
    }

    private var result: some View {
        VStack(alignment: .leading, spacing: DS.Space.s2) {
            Text(task.status == "failed" ? "Didn't finish" : task.verdictWords)
                .font(DS.Font.body).foregroundStyle(DS.Color.text)
            if let summary = task.review.summary, !summary.isEmpty {
                Text(summary).font(DS.Font.caption).foregroundStyle(DS.Color.text)
            } else if !task.summary.isEmpty {
                Text(task.summary).font(DS.Font.caption).foregroundStyle(DS.Color.textDim).lineLimit(8)
            }
            if !task.changed.isEmpty {
                Text("Branch \(task.branch) · \(task.diffstat) · not pushed")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textDim).textSelection(.enabled)
            }
            ForEach(task.outside, id: \.self) { path in
                Text("Changed outside the project: \(path)").font(DS.Font.caption).foregroundStyle(DS.Color.text)
            }
            if task.secretInDiff {
                Text("The change holds something that looks like a credential.")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.text)
            }
            ForEach(task.denials, id: \.self) { denial in
                Text("Blocked by the fence: \(denial)").font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }
            ForEach(task.review.concerns ?? [], id: \.self) { concern in
                Text("Check: \(concern)").font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }
            if let next = task.review.next, !next.isEmpty {
                Text("Next: \(next)").font(DS.Font.caption).foregroundStyle(DS.Color.text)
            }
            if !task.error.isEmpty {
                Text(task.error).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            }
        }
    }

    private func section<Content: View>(_ label: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: DS.Space.s2) {
            Text(label.uppercased()).font(DS.Font.label).tracking(0.5).foregroundStyle(DS.Color.textFaint)
            content()
        }
    }

    private func row(_ what: String, _ who: String, _ when: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: DS.Space.s3) {
            Text(what).font(DS.Font.caption).foregroundStyle(DS.Color.text)
                .frame(width: 210, alignment: .leading)
            Text(who).font(DS.Font.caption).foregroundStyle(DS.Color.text)
            Spacer()
            Text(when).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
        }
    }
}

private func seconds(_ s: Double) -> String {
    s >= 60 ? "\(Int(s) / 60)m \(String(format: "%02d", Int(s) % 60))s" : String(format: "%.1f s", s)
}

private func ago(_ stamp: Double) -> String {
    let minutes = Int((Date().timeIntervalSince1970 - stamp) / 60)
    if minutes < 1 { return "just now" }
    if minutes < 60 { return "\(minutes)m ago" }
    if minutes < 60 * 24 { return "\(minutes / 60)h ago" }
    return "\(minutes / 1440)d ago"
}
