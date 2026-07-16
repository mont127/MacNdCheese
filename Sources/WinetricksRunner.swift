import SwiftUI

/// Drives a `winetricks_run` backend job and streams its progress, the same
/// job-polling shape as `InstallRunner` but against `runWinetricks` instead
/// of `runInstaller` — kept as a separate small class rather than bending
/// `InstallRunner`'s installer.sh-specific signature (DXVK/Mesa paths have no
/// meaning for a winetricks verb) to also cover this case.
@MainActor
final class WinetricksRunner: ObservableObject {
    @Published var isRunning = false
    @Published var logLines: [String] = []
    @Published var currentVerb = ""
    @Published var done = false
    @Published var failed = false

    private var logOffset = 0
    private var jobId: String?

    /// Run the given winetricks verbs against `prefix`, streaming progress
    /// until the job finishes. No-op while a run is already in flight or
    /// when there are no verbs.
    func run(verbs: [String], force: Bool = false, prefix: String, backend: BackendClient) async {
        guard !isRunning, !verbs.isEmpty else { return }
        reset()
        isRunning = true

        guard let id = await backend.runWinetricks(prefix: prefix, verbs: verbs, force: force) else {
            failed = true
            done = true
            isRunning = false
            return
        }
        jobId = id

        // Same tolerance policy as InstallRunner: a transient transport/decode
        // hiccup isn't completion, but repeated failures must still resolve to
        // a terminal state so the sheet never gets stuck showing "installing".
        var consecutiveFailures = 0
        while true {
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard let progress = await backend.getInstallProgress(jobId: id, offset: logOffset) else {
                consecutiveFailures += 1
                if consecutiveFailures >= 10 {
                    logLines.append(L("Lost contact with the installer."))
                    failed = true
                    done = true
                    isRunning = false
                    break
                }
                continue
            }
            consecutiveFailures = 0
            logLines.append(contentsOf: progress.lines)
            logOffset = progress.totalLines
            currentVerb = progress.current
            if progress.done {
                done = true
                failed = progress.failed
                isRunning = false
                break
            }
        }
    }

    func cancel(backend: BackendClient) async {
        guard let id = jobId else { return }
        await backend.winetricksCancel(jobId: id)
    }

    /// Clear log/flags before a fresh run. Does not touch `isRunning`.
    func reset() {
        logLines = []
        logOffset = 0
        currentVerb = ""
        done = false
        failed = false
        jobId = nil
    }
}
