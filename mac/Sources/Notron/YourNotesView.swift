import SwiftUI

/// Onboarding step 2 and Settings → "Your notes": every note on the left with a
/// three-way switch, and on the right what is actually inside the selected one.
///
/// The panel is the whole reason this screen is usable on a real library: half
/// these notes were last touched in 2019 and their titles say nothing. Judging
/// them used to mean the Notes app open alongside this window, hunting for each
/// one by hand. The text comes straight from Notes on a click the user made —
/// no model reads a preview, and none of it is stored.
struct YourNotesView: View {
    @StateObject private var model = LibraryModel()
    @State private var query = ""
    @State private var saved = false
    @FocusState private var searching: Bool
    @Environment(\.dismiss) private var dismiss

    private var years: [Int?] {
        let now = Calendar.current.component(.year, from: Date())
        return [nil] + Array((now - 12)...now).reversed()
    }

    private var shown: [LibraryNote] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        return q.isEmpty ? model.notes
            : model.notes.filter { $0.title.lowercased().contains(q) || $0.folder.lowercased().contains(q) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Space.s4) {
            header
            controls
            HStack(alignment: .top, spacing: DS.Space.s4) {
                list.frame(width: 420)
                preview
            }
            footer
        }
        .padding(DS.Space.s6)
        .frame(minWidth: 1000, idealWidth: 1060, minHeight: 620, idealHeight: 700)
        .background(DS.Color.bg)
        .focusable()
        .focusEffectDisabled()
        .onKeyPress(action: key)
        .onAppear { model.load() }
    }

    // ------------------------------------------------------------ keyboard
    //
    // 300 notes is a triage job, not a form: down-arrow, read the panel,
    // press 1/2/3, down-arrow again. The search field keeps its own keys.

    private func key(_ press: KeyPress) -> KeyPress.Result {
        guard !searching else { return .ignored }
        switch press.key {
        case .upArrow: return move(-1)
        case .downArrow: return move(1)
        default: break
        }
        switch press.characters {
        case "1": return assign(.home)
        case "2": return assign(.read)
        case "3": return assign(.ignore)
        default: return .ignored
        }
    }

    private func move(_ step: Int) -> KeyPress.Result {
        let rows = shown
        guard !rows.isEmpty else { return .handled }
        let here = rows.firstIndex { $0.id == model.selected }
        let next = here.map { min(max($0 + step, 0), rows.count - 1) } ?? 0
        model.select(rows[next].id)
        return .handled
    }

    private func assign(_ state: LibraryNote.State) -> KeyPress.Result {
        guard let id = model.selected else { return .ignored }
        model.set(id, to: state)
        return .handled
    }

    // -------------------------------------------------------------- chrome

    private var header: some View {
        VStack(alignment: .leading, spacing: DS.Space.s1) {
            Text("Your notes").font(DS.Font.headline).foregroundStyle(DS.Color.text)
            Text("Home = she may file things here. Read only = she may read it to answer you. Ignore = she never reads it.")
                .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            if model.privateCount > 0 {
                Text("We spotted \(model.privateCount) that look private — they're set to Ignore.")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.accent)
            }
        }
    }

    private var controls: some View {
        HStack(spacing: DS.Space.s3) {
            TextField("Search", text: $query)
                .textFieldStyle(.plain)
                .font(DS.Font.body)
                .focused($searching)
                .padding(.horizontal, DS.Space.s3).padding(.vertical, DS.Space.s2)
                .background(DS.Color.surface)
                .overlay(RoundedRectangle(cornerRadius: DS.Radius.md).stroke(DS.Color.hairline))
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.md))
            Spacer()
            Text("Start from").font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            Picker("Start from", selection: $model.startFrom) {
                ForEach(years, id: \.self) { year in
                    Text(year.map(String.init) ?? "everything").tag(year)
                }
            }
            .labelsHidden()
            .frame(width: 130)
            .onChange(of: model.startFrom) { _, _ in model.applyStartFrom() }
        }
    }

    // ---------------------------------------------------------------- list

    private var list: some View {
        Group {
            if model.loading {
                VStack(spacing: DS.Space.s2) {
                    ProgressView()
                    Text("Reading your notes… a cold Notes app can take a moment.")
                        .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let problem = model.problem {
                Text(problem).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 0) {
                            ForEach(shown) { note in
                                row(note).id(note.id)
                                Rectangle().fill(DS.Color.hairline).frame(height: 1)
                            }
                        }
                    }
                    // Arrowing past the bottom of the window has to bring the
                    // row with it, or the panel describes a note nobody can see.
                    .onChange(of: model.selected) { _, id in
                        guard let id else { return }
                        withAnimation(DS.Motion.standard) { proxy.scrollTo(id, anchor: .center) }
                    }
                }
                .background(DS.Color.surface)
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
                .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairline))
            }
        }
        .frame(maxHeight: .infinity)
    }

    /// One line of title, one of context, and a single word for the state.
    ///
    /// The three-way control used to sit in every row. Two hundred of them read
    /// as noise, and the highlight behind one was only as wide as its own text —
    /// a long title got a wider selection than a short one. Rows are now uniform
    /// and full width, the state is one word, and the control itself lives once,
    /// in the panel, for the note being looked at.
    private func row(_ note: LibraryNote) -> some View {
        let picked = model.selected == note.id
        return HStack(spacing: 0) {
            Rectangle().fill(DS.Color.accent).frame(width: 3).opacity(picked ? 1 : 0)
            HStack(alignment: .top, spacing: DS.Space.s3) {
                VStack(alignment: .leading, spacing: DS.Space.s1) {
                    Text(note.title).font(DS.Font.body).foregroundStyle(DS.Color.text).lineLimit(1)
                    Text("\(note.folder) · \(Self.shortDate(note.modified))")
                        .font(DS.Font.caption).foregroundStyle(DS.Color.textDim).lineLimit(1)
                    if !note.reason.isEmpty {
                        Text(note.reason).font(DS.Font.label).foregroundStyle(DS.Color.textFaint)
                    }
                }
                Spacer(minLength: DS.Space.s2)
                stateWord(note.state)
            }
            .padding(.horizontal, DS.Space.s3).padding(.vertical, DS.Space.s3)
            .frame(maxWidth: .infinity, alignment: .leading)    // every row the same width
        }
        .background(picked ? DS.Color.surfaceAlt : Color.clear)
        .contentShape(Rectangle())
        .onTapGesture { model.select(note.id) }
        .opacity(note.state == .ignore ? 0.55 : 1)      // state by opacity, never by hue
        .animation(DS.Motion.standard, value: note.state)
    }

    /// Read only is the default on almost every row, so it stays quiet; the
    /// handful of homes are the ones worth finding at a glance.
    @ViewBuilder
    private func stateWord(_ state: LibraryNote.State) -> some View {
        switch state {
        case .home:
            Text(state.label)
                .font(DS.Font.label)
                .foregroundStyle(DS.Color.bg)
                .padding(.horizontal, DS.Space.s2).padding(.vertical, DS.Space.s1)
                .background(DS.Color.accent)
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.sm))
        case .read:
            Text(state.label).font(DS.Font.label).foregroundStyle(DS.Color.textDim)
        case .ignore:
            Text(state.label).font(DS.Font.label).foregroundStyle(DS.Color.textFaint)
        }
    }

    /// Apple hands back "Tuesday, September 1, 2026 at 10:53:49 PM". At the
    /// width of a list row that is the widest thing on screen and says least.
    static func shortDate(_ modified: String) -> String {
        // Apple writes a narrow no-break space (U+202F) before AM/PM. Python's
        // strptime shrugs at it; DateFormatter with a fixed format does not, and
        // silently fails to parse every date the app is given.
        let clean = modified
            .replacingOccurrences(of: "\u{202F}", with: " ")
            .replacingOccurrences(of: "\u{00A0}", with: " ")
        for pattern in ["EEEE, d MMMM yyyy 'at' HH:mm:ss", "EEEE, MMMM d, yyyy 'at' h:mm:ss a"] {
            let reader = DateFormatter()
            reader.locale = Locale(identifier: "en_US_POSIX")
            reader.dateFormat = pattern
            if let date = reader.date(from: clean) {
                let writer = DateFormatter()
                writer.dateFormat = "d MMM yyyy"
                return writer.string(from: date)
            }
        }
        return modified
    }

    // ------------------------------------------------------- preview panel

    private var preview: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let note = model.selectedNote {
                previewHeader(note)
                Rectangle().fill(DS.Color.hairline).frame(height: 1)
                previewBody(note)
            } else {
                VStack(spacing: DS.Space.s2) {
                    Text("Pick a note").font(DS.Font.body).foregroundStyle(DS.Color.textDim)
                    Text("What's inside it shows here, so you never have to open Notes to decide.")
                        .font(DS.Font.caption).foregroundStyle(DS.Color.textFaint)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(DS.Space.s6)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Color.surface)
        .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
        .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairline))
    }

    private func previewHeader(_ note: LibraryNote) -> some View {
        VStack(alignment: .leading, spacing: DS.Space.s1) {
            HStack(alignment: .firstTextBaseline, spacing: DS.Space.s3) {
                Text(note.title).font(DS.Font.title).foregroundStyle(DS.Color.text).lineLimit(2)
                Spacer()
                Button("Open in Notes") { model.openInNotes(note.id) }
                    .buttonStyle(.plain)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Color.accent)
            }
            Text("\(note.folder) · \(Self.shortDate(note.modified))")
                .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
            Segmented(values: LibraryNote.State.allCases,
                      selection: note.state,
                      label: { $0.label },
                      choose: { model.set(note.id, to: $0) })
                .padding(.top, DS.Space.s2)
            Text("or press 1, 2 or 3")
                .font(DS.Font.label).foregroundStyle(DS.Color.textFaint)
        }
        .padding(DS.Space.s4)
    }

    /// Why this note's text is off the screen, in the user's words — nil once
    /// they have asked to see it. The title check saves a read; the body check
    /// catches the note whose title gave nothing away.
    private func heldBecause(_ note: LibraryNote) -> String? {
        if model.revealed.contains(note.id) { return nil }
        if note.sensitive { return "This one looks private." }
        if model.previewHeld { return "There's something in here that looks like a password or a key." }
        return nil
    }

    @ViewBuilder
    private func previewBody(_ note: LibraryNote) -> some View {
        if let why = heldBecause(note) {
            held(note, why)
        } else if model.previewLoading {
            ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let problem = model.previewProblem {
            Text(problem)
                .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(DS.Space.s6)
        } else {
            ScrollView {
                VStack(alignment: .leading, spacing: DS.Space.s3) {
                    Text(model.previewText.isEmpty ? "This note is empty." : model.previewText)
                        .font(DS.Font.caption)
                        .foregroundStyle(model.previewText.isEmpty ? DS.Color.textFaint : DS.Color.text)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if model.previewTruncated {
                        Text("Cut off here — open it in Notes to read the rest.")
                            .font(DS.Font.label).foregroundStyle(DS.Color.textFaint)
                    }
                }
                .padding(DS.Space.s4)
            }
            .frame(maxHeight: .infinity)
        }
    }

    /// Notron not reading a note is only half of it. Putting its contents on a
    /// wide screen because an arrow key drifted onto the row undoes the rest,
    /// in the way that still matters: the room the user is sitting in.
    private func held(_ note: LibraryNote, _ why: String) -> some View {
        VStack(spacing: DS.Space.s3) {
            Text(why)
                .font(DS.Font.body).foregroundStyle(DS.Color.text)
                .multilineTextAlignment(.center)
            Text("Notron doesn't read it. We're keeping it off your screen too.")
                .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                .multilineTextAlignment(.center)
            Button("Show it anyway") { model.reveal(note.id) }
                .buttonStyle(.plain)
                .font(DS.Font.body)
                .foregroundStyle(DS.Color.bg)
                .padding(.horizontal, DS.Space.s5).padding(.vertical, DS.Space.s3)
                .background(DS.Color.accent)
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.md))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(DS.Space.s6)
    }

    // -------------------------------------------------------------- footer

    private var footer: some View {
        HStack(alignment: .bottom) {
            VStack(alignment: .leading, spacing: DS.Space.s1) {
                let c = model.counts
                Text("\(c.home) homes · \(c.read) read only · \(c.ignore) ignored")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                Text("Previews are for you alone — Notron never reads one.")
                    .font(DS.Font.label).foregroundStyle(DS.Color.textFaint)
            }
            Spacer()
            if saved {
                Text("Saved").font(DS.Font.caption).foregroundStyle(DS.Color.success)
            }
            Button("Done") {
                do {
                    try model.save()
                    saved = true
                    dismiss()
                } catch {
                    model.problem = "Couldn't save: \(error)"
                }
            }
            .buttonStyle(.plain)
            .font(DS.Font.body)
            .foregroundStyle(DS.Color.bg)
            .padding(.horizontal, DS.Space.s5).padding(.vertical, DS.Space.s3)
            .background(DS.Color.accent)
            .clipShape(RoundedRectangle(cornerRadius: DS.Radius.md))
            .disabled(model.loading || model.notes.isEmpty)
        }
    }
}
