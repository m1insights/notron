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
        try FileManager.default.createDirectory(at: Self.file.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try encoder.encode(file).write(to: Self.file, options: .atomic)
    }

    /// Apple Notes dates read "Tuesday, 1 September 2026 at 16:03:12" (or the
    /// US form); the year is the only four-digit run in either.
    static func year(of modified: String) -> Int? {
        let digits = modified.split(whereSeparator: { !$0.isNumber })
        return digits.compactMap { $0.count == 4 ? Int($0) : nil }.first
    }
}
