import Foundation
import CoreServices

let appData = ProcessInfo.processInfo.environment["SPLITRAIL_DATA_DIR"].map { URL(fileURLWithPath: $0) }
    ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/splitrail-desktop")

func savePrivate(_ data: Data, to url: URL) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                           attributes: [.posixPermissions: 0o700])
    try data.write(to: url, options: .atomic)
    try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
}

/// One disposable accounting process. No polling loop, shell, or retained Python interpreter.
final class Worker {
    private var process: Process?
    private var timeout: DispatchWorkItem?
    private var directory: URL?
    var busy: Bool { process != nil }

    func run(_ task: String, options: [String: Any] = [:], completion: @escaping (Result<[String: Any], Error>) -> Void) {
        guard !busy else { completion(.failure(NativeError.message("Another operation is running."))); return }
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("splitrail-native-worker-" + UUID().uuidString)
        do {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
            let request = root.appendingPathComponent("request.json"), response = root.appendingPathComponent("response.json")
            try savePrivate(JSONSerialization.data(withJSONObject: ["task": task, "options": options]), to: request)
            let child = Process()
            if let python = ProcessInfo.processInfo.environment["SPLITRAIL_NATIVE_PYTHON"],
               let source = ProcessInfo.processInfo.environment["SPLITRAIL_NATIVE_SOURCE"] {
                child.executableURL = URL(fileURLWithPath: python)
                child.arguments = ["-m", "splitrail_desktop.native_worker", request.path, response.path]
                child.environment = ProcessInfo.processInfo.environment.merging(["PYTHONPATH": source]) { _, new in new }
            } else {
                child.executableURL = Bundle.main.resourceURL!.appendingPathComponent("worker/splitrail-worker")
                child.arguments = [request.path, response.path]
                child.environment = ProcessInfo.processInfo.environment
            }
            child.environment?["SPLITRAIL_DATA_DIR"] = appData.path
            child.qualityOfService = .utility
            child.standardInput = FileHandle.nullDevice
            child.standardOutput = FileHandle.nullDevice
            child.standardError = FileHandle.nullDevice
            child.terminationHandler = { [weak self] finished in
                DispatchQueue.main.async {
                    guard let self, self.process === finished else { return }
                    self.timeout?.cancel(); self.timeout = nil; self.process = nil; self.directory = nil
                    defer { try? FileManager.default.removeItem(at: root) }
                    do {
                        guard finished.terminationStatus == 0 else { throw NativeError.message("Operation stopped. Previous data is retained.") }
                        let attrs = try FileManager.default.attributesOfItem(atPath: response.path)
                        guard (attrs[.size] as? NSNumber)?.intValue ?? Int.max <= 25 * 1024 * 1024 else {
                            throw NativeError.message("Worker result exceeds the size limit.")
                        }
                        let payload = try JSONSerialization.jsonObject(with: Data(contentsOf: response)) as? [String: Any] ?? [:]
                        if let error = payload["error"] as? String { throw NativeError.message(error) }
                        guard let result = payload["result"] as? [String: Any] else { throw NativeError.message("Worker returned no result.") }
                        completion(.success(result))
                    } catch { completion(.failure(error)) }
                }
            }
            process = child; directory = root
            try child.run()
            let expiry = DispatchWorkItem { [weak self, weak child] in
                guard let self, let child, self.process === child, child.isRunning else { return }
                self.stop(child)
            }
            timeout = expiry
            DispatchQueue.main.asyncAfter(deadline: .now() + (task == "quota" ? 35 : task == "sync" ? 240 : 120), execute: expiry)
        } catch {
            process = nil; directory = nil
            try? FileManager.default.removeItem(at: root)
            completion(.failure(error))
        }
    }
    private func stop(_ child: Process) {
        let pid = child.processIdentifier
        // The headless worker calls setsid(). Its private group contains only our
        // disposable collectors/read-only quota queries, never existing user turns.
        let ownsGroup = getpgid(pid) == pid
        if ownsGroup { kill(-pid, SIGTERM) } else { child.terminate() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            if ownsGroup { kill(-pid, SIGKILL) } else if child.isRunning { kill(pid, SIGKILL) }
        }
    }
    func cancel() {
        timeout?.cancel()
        if let process, process.isRunning { stop(process) }
        if let directory { try? FileManager.default.removeItem(at: directory) }
    }
}

/// Batched kernel notifications replace recursive directory polling.
final class LogEvents {
    private var stream: FSEventStreamRef?
    private var relevantPaths: [String] = []
    var changed: (() -> Void)?
    func start() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let codex = ProcessInfo.processInfo.environment["CODEX_HOME"].map { URL(fileURLWithPath: $0) } ?? home.appendingPathComponent(".codex")
        let candidates = [codex.appendingPathComponent("sessions"), codex.appendingPathComponent("archived_sessions"),
                          home.appendingPathComponent(".pi/agent/sessions"), home.appendingPathComponent(".claude/projects")]
        relevantPaths = candidates.map { $0.path }
        // Watching the nearest existing parent also catches a directory created later.
        let paths = Set(candidates.compactMap { candidate -> String? in
            var path = candidate
            while !FileManager.default.fileExists(atPath: path.path), path.path != home.path { path.deleteLastPathComponent() }
            // Never subscribe to the entire home directory for an uninstalled tool.
            guard path.path != home.path else { return nil }
            return path.path
        })
        guard !paths.isEmpty else { return }
        var context = FSEventStreamContext(version: 0, info: Unmanaged.passUnretained(self).toOpaque(), retain: nil, release: nil, copyDescription: nil)
        stream = FSEventStreamCreate(nil, { _, info, count, rawPaths, _, _ in
            guard let info else { return }
            let observer = Unmanaged<LogEvents>.fromOpaque(info).takeUnretainedValue()
            let paths = unsafeBitCast(rawPaths, to: NSArray.self) as? [String] ?? []
            if paths.prefix(count).contains(where: { path in
                observer.relevantPaths.contains { path == $0 || path.hasPrefix($0 + "/") || $0.hasPrefix(path + "/") }
            }) { observer.changed?() }
        }, &context, Array(paths) as CFArray, FSEventStreamEventId(kFSEventStreamEventIdSinceNow), 5,
        FSEventStreamCreateFlags(kFSEventStreamCreateFlagWatchRoot | kFSEventStreamCreateFlagUseCFTypes | kFSEventStreamCreateFlagFileEvents))
        if let stream {
            FSEventStreamSetDispatchQueue(stream, DispatchQueue.main)
            if !FSEventStreamStart(stream) { FSEventStreamInvalidate(stream); FSEventStreamRelease(stream); self.stream = nil }
        }
    }
    deinit { if let stream { FSEventStreamStop(stream); FSEventStreamInvalidate(stream); FSEventStreamRelease(stream) } }
}
