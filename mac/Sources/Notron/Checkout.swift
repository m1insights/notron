import Foundation

/// Where the Python core lives. The app is built from inside a clone of the
/// repo, so the checkout is found by walking up from the app bundle (or, under
/// `swift run`, from this source file) to the `pyproject.toml` named "notron".
/// `NOTRON_HOME` / `NOTRON_PYTHON` still win: they are the seam a bundled
/// runtime will use (see mac/README.md).
public enum Checkout {
    public static func home(environment: [String: String] = ProcessInfo.processInfo.environment,
                            startingPoints: [URL] = defaultStartingPoints) -> URL {
        if let path = environment["NOTRON_HOME"], !path.isEmpty {
            return URL(fileURLWithPath: path)
        }
        for start in startingPoints {
            if let found = find(from: start) { return found }
        }
        // Nothing found: the current directory, so `run` fails with Python's own
        // "No module named notron" rather than a path nobody has.
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
    }

    public static func python(environment: [String: String] = ProcessInfo.processInfo.environment,
                              home: URL) -> String {
        if let path = environment["NOTRON_PYTHON"], !path.isEmpty { return path }
        return home.appendingPathComponent(".venv/bin/python").path
    }

    public static var defaultStartingPoints: [URL] {
        [Bundle.main.bundleURL, URL(fileURLWithPath: #filePath)]
    }

    /// The nearest directory at or above `start` holding Notron's pyproject.
    /// Checking the name matters: a clone kept inside another Python project
    /// would otherwise stop one level too high.
    static func find(from start: URL) -> URL? {
        // Walk by path, not URL: `deletingLastPathComponent` on "/" yields
        // "/../", so a URL walk never reaches its own end and the app hangs
        // whenever no checkout is above it (caught by the test, 2026-10-02).
        var dir = start.standardizedFileURL.path
        while true {
            let manifest = (dir as NSString).appendingPathComponent("pyproject.toml")
            if let text = try? String(contentsOfFile: manifest, encoding: .utf8),
               text.range(of: #"(?m)^name\s*=\s*"notron"\s*$"#, options: .regularExpression) != nil {
                return URL(fileURLWithPath: dir, isDirectory: true)
            }
            let parent = (dir as NSString).deletingLastPathComponent
            if parent == dir || parent.isEmpty { return nil }
            dir = parent
        }
    }
}
