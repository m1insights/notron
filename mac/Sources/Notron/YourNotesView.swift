import SwiftUI

/// Onboarding step 2 and Settings → "Your notes": every note, a three-way
/// switch per row, pre-filled with Notron's guesses. Nothing here reads a note
/// body and nothing calls a model — the core's `library.suggest` is plain code.
struct YourNotesView: View {
    @StateObject private var model = LibraryModel()
    @State private var query = ""
    @State private var saved = false
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
            list
            footer
        }
        .padding(DS.Space.s6)
        .frame(width: 720, height: 520)
        .background(DS.Color.bg)
        .onAppear { model.load() }
    }

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
                .padding(.horizontal, DS.Space.s3).padding(.vertical, DS.Space.s2)
                .background(DS.Color.surface)
                .overlay(RoundedRectangle(cornerRadius: DS.Radius.md).stroke(DS.Color.hairlineLight))
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

    private var list: some View {
        Group {
            if model.loading {
                VStack(spacing: DS.Space.s2) {
                    ProgressView()
                    Text("Reading your notes… a cold Notes app can take a moment.")
                        .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let problem = model.problem {
                Text(problem).font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(shown) { note in
                            row(note)
                            Rectangle().fill(DS.Color.hairlineLight).frame(height: 1)
                        }
                    }
                }
                .background(DS.Color.surface)
                .clipShape(RoundedRectangle(cornerRadius: DS.Radius.lg))
                .overlay(RoundedRectangle(cornerRadius: DS.Radius.lg).stroke(DS.Color.hairlineLight))
            }
        }
    }

    private func row(_ note: LibraryNote) -> some View {
        HStack(alignment: .center, spacing: DS.Space.s3) {
            VStack(alignment: .leading, spacing: DS.Space.s1) {
                Text(note.title).font(DS.Font.body).foregroundStyle(DS.Color.text).lineLimit(1)
                Text("\(note.folder) · \(note.modified)")
                    .font(DS.Font.caption).foregroundStyle(DS.Color.textDim).lineLimit(1)
                if !note.reason.isEmpty {
                    Text(note.reason).font(DS.Font.label).foregroundStyle(DS.Color.textFaint)
                }
            }
            Spacer()
            Picker("", selection: Binding(
                get: { note.state },
                set: { model.set(note.id, to: $0) }
            )) {
                ForEach(LibraryNote.State.allCases, id: \.self) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 260)
        }
        .padding(.horizontal, DS.Space.s4).padding(.vertical, DS.Space.s3)
        .opacity(note.state == .ignore ? 0.55 : 1)      // state by opacity, never by hue
        .animation(DS.Motion.standard, value: note.state)
    }

    private var footer: some View {
        HStack {
            let c = model.counts
            Text("\(c.home) homes · \(c.read) read only · \(c.ignore) ignored")
                .font(DS.Font.caption).foregroundStyle(DS.Color.textDim)
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
