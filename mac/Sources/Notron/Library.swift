import Foundation

/// One note as the core reports it from `notron library scan`.
struct LibraryNote: Identifiable, Codable, Equatable {
    enum State: String, Codable, CaseIterable {
        case home, read, ignore

        var label: String {
            switch self {
            case .home: return "Home"
            case .read: return "Read only"
            case .ignore: return "Ignore"
            }
        }
    }

    let id: String
    let title: String
    let folder: String
    let modified: String
    var state: State
    let suggested: State
    let reason: String
    /// A note `privacy.py` reads as a credentials store. The preview panel
    /// holds these behind one click — a password should not be on screen
    /// because an arrow key drifted onto its row.
    let sensitive: Bool
}

/// One note's body, plain text, as `notron library peek` hands it over.
struct NotePeek: Codable {
    let id: String
    let text: String
    let chars: Int
    let truncated: Bool
    /// Non-empty when the core kept the text back because the body looks like
    /// credentials — a title check alone misses a note called "CRITICAL".
    let held: String
}

struct LibraryScan: Codable {
    var notes: [LibraryNote]
    var duplicates: [String: [String]]
    var startFrom: String?
    var configured: Bool
}

/// What `.notron/library.json` holds — the contract with `notron/library.py`.
/// Encoded with snake_case keys so the two sides never drift.
struct LibraryFile: Codable {
    var homes: [String]
    var ignore: [String]
    var decided: [String]
    var startFrom: String?
    var chosenAt: String
}

@MainActor
final class LibraryModel: ObservableObject {
    @Published var notes: [LibraryNote] = []
    @Published var duplicates: [String: [String]] = [:]
    @Published var startFrom: Int? = nil        // a year; nil = everything
    @Published var loading = false
    @Published var problem: String?

    // --- the preview panel: what's actually inside the selected note, so a
    // decade-old note can be judged without opening Notes beside the window.
    @Published var selected: String?
    @Published var previewText = ""
    @Published var previewLoading = false
    @Published var previewProblem: String?
    @Published var previewTruncated = false
    /// The core held this note's text back: its body looks like credentials.
    @Published var previewHeld = false
    /// Password-ish notes the user has explicitly asked to see this session.
    /// Never persisted — the next time the window opens they are held again.
    @Published var revealed: Set<String> = []
    /// Read once, kept for the window's lifetime: arrowing up and down a list
    /// should not cost a fresh AppleScript round trip per row.
    private var peeks: [String: NotePeek] = [:]

    static let file = Core.home.appendingPathComponent(".notron/library.json")

    static var exists: Bool { FileManager.default.fileExists(atPath: file.path) }

    var privateCount: Int { notes.filter { $0.suggested == .ignore }.count }
    var counts: (home: Int, read: Int, ignore: Int) {
        (notes.filter { $0.state == .home }.count,
         notes.filter { $0.state == .read }.count,
         notes.filter { $0.state == .ignore }.count)
    }

    /// Asks the core for every note and its state. Off the main thread: a
    /// Notes listing is a second or two, and a cold Notes app can be forty.
    func load() {
        loading = true
        problem = nil
        Task.detached { [weak self] in
            let result: Result<LibraryScan, Error> = Result {
                let json = try Core.run(["library", "scan"])
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase
                return try decoder.decode(LibraryScan.self, from: Data(json.utf8))
            }
            await MainActor.run {
                guard let self else { return }
                self.loading = false
                switch result {
                case .success(let scan):
                    self.notes = scan.notes
                    self.duplicates = scan.duplicates
                    self.startFrom = scan.startFrom.flatMap { Int($0.prefix(4)) }
                    self.select(scan.notes.first?.id)
                case .failure(let error):
                    self.problem = "\(error)"
                }
            }
        }
    }

    /// Flip one row. Only one note of a shared title can be a home, so
    /// choosing one drops its twins back to read only — the inline picker.
    func set(_ id: String, to state: LibraryNote.State) {
        guard let i = notes.firstIndex(where: { $0.id == id }) else { return }
        notes[i].state = state
        if state == .home, let twins = duplicates[notes[i].title] {
            for twin in twins where twin != id {
                if let j = notes.firstIndex(where: { $0.id == twin }), notes[j].state == .home {
                    notes[j].state = .read
                }
            }
        }
    }

    /// "Start from 2026": one move, every note last edited before it becomes
    /// ignore. Rows can be flipped back afterwards; the core honours the row.
    func applyStartFrom() {
        guard let year = startFrom else { return }
        for i in notes.indices where Self.year(of: notes[i].modified).map({ $0 < year }) ?? false {
            notes[i].state = .ignore
        }
    }

    var selectedNote: LibraryNote? { notes.first { $0.id == selected } }

    /// Select a row and fill the panel. A sensitive note is selected but not
    /// read — nothing is fetched until the user presses "Show it anyway".
    func select(_ id: String?) {
        guard selected != id else { return }
        selected = id
        previewProblem = nil
        previewText = ""
        previewTruncated = false
        previewHeld = false
        previewLoading = false
        loadPreview()
    }

    /// "Show it anyway". The core held the text back, so this is a fresh read
    /// with `--reveal` — the cached, empty answer would say nothing.
    func reveal(_ id: String) {
        revealed.insert(id)
        peeks[id] = nil
        previewHeld = false
        loadPreview()
    }

    private func loadPreview() {
        guard let id = selected, let note = selectedNote else { return }
        guard !note.sensitive || revealed.contains(id) else { return }
        if let cached = peeks[id] {
            previewText = cached.text
            previewTruncated = cached.truncated
            previewHeld = !cached.held.isEmpty
            return
        }
        let asked = revealed.contains(id)
        previewLoading = true
        Task.detached { [weak self] in
            let result: Result<NotePeek, Error> = Result {
                let json = try Core.run(["library", "peek", id] + (asked ? ["--reveal"] : []))
                return try JSONDecoder().decode(NotePeek.self, from: Data(json.utf8))
            }
            await MainActor.run {
                guard let self, self.selected == id else { return }   // the user moved on
                self.previewLoading = false
                switch result {
                case .success(let peek):
                    self.peeks[id] = peek
                    self.previewText = peek.text
                    self.previewTruncated = peek.truncated
                    self.previewHeld = !peek.held.isEmpty
                case .failure(let error):
                    self.previewProblem = "Couldn't read this note: \(error)"
                }
            }
        }
    }

    /// The escape hatch: the real note, in the real Notes app.
    func openInNotes(_ id: String) {
        Task.detached { _ = try? Core.run(["library", "open", id]) }
    }

    func save() throws {
        let file = LibraryFile(
            homes: notes.filter { $0.state == .home }.map(\.id),
            ignore: notes.filter { $0.state == .ignore }.map(\.id),
            decided: notes.map(\.id),
            startFrom: startFrom.map { "\($0)-01-01" },
            chosenAt: ISO8601DateFormatter().string(from: Date())
        )
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        // The Python writer validates, fsyncs and retains the last validated
        // backup, preserving setup IDs/settings this screen does not edit.
        _ = try Core.run(["library", "save"], input: encoder.encode(file))
    }

    /// Apple Notes dates read "Tuesday, 1 September 2026 at 16:03:12" (or the
    /// US form); the year is the only four-digit run in either.
    static func year(of modified: String) -> Int? {
        let digits = modified.split(whereSeparator: { !$0.isNumber })
        return digits.compactMap { $0.count == 4 ? Int($0) : nil }.first
    }
}
