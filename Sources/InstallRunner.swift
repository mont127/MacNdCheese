import SwiftUI

/// Drives `installer.sh` through the backend and streams its progress. Extracted
/// so the first-run onboarding flow and any other caller share one implementation
/// of "run these install actions and show the live log" instead of duplicating
/// the job-polling loop.
@MainActor
final class InstallRunner: ObservableObject {
    @Published var isRunning = false
    @Published var logLines: [String] = []
    @Published var currentAction = ""
    @Published var done = false
    @Published var failed = false

    private var logOffset = 0

    /// Run the given installer.sh actions (`install_*` / `uninstall_*`) against the
    /// active prefix, streaming progress until the job finishes. No-op while a run
    /// is already in flight or when there are no actions.
    func run(actions: [String], backend: BackendClient) async {
        guard !isRunning, !actions.isEmpty else { return }
        guard let installerPath = InstallerPathStore.installerScriptPath() else {
            reset()
            logLines = [L("installer.sh not found — reinstall MacNCheese.")]
            failed = true
            done = true
            return
        }

        reset()
        isRunning = true

        let prefix = backend.activePrefix ?? NSHomeDirectory() + "/wined"
        let p = InstallerPathStore.current()

        guard let jobId = await backend.runInstaller(
            installerPath: installerPath,
            actions: actions,
            prefix: prefix,
            dxvkSrc: p.dxvkSrc,
            dxvk64: p.dxvkInstall64,
            dxvk32: p.dxvkInstall32,
            mesa: p.mesaDir,
            mesaUrl: InstallerPathStore.mesaURL,
            dxmt: p.dxmtDir,
            vkd3d: p.vkd3dDir,
            gptkDir: p.gptkDir
        ) else {
            failed = true
            done = true
            isRunning = false
            return
        }

        // getInstallProgress returns nil on a transient transport/decode hiccup,
        // NOT on completion (that arrives as progress.done). Tolerate a few in a
        // row, but if contact is genuinely lost, end in a terminal FAILED state —
        // never leave isRunning stuck true, or the onboarding sheet (which gates
        // dismissal on isRunning and its button on done) would trap the user.
        var consecutiveFailures = 0
        while true {
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard let progress = await backend.getInstallProgress(jobId: jobId, offset: logOffset) else {
                consecutiveFailures += 1
                if consecutiveFailures >= 10 {   // ~5s of repeated failures
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
            currentAction = progress.current
            if progress.done {
                done = true
                failed = progress.failed
                isRunning = false
                await backend.loadStatus()
                break
            }
        }
    }

    /// Clear log/flags before a fresh run. Does not touch `isRunning`.
    func reset() {
        logLines = []
        logOffset = 0
        currentAction = ""
        done = false
        failed = false
    }
}
