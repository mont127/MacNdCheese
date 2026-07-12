import SwiftUI
import Foundation

/// Launch-time wine version gate. A marker file at
/// `~/Library/Application Support/MacNCheese/wine_version` records which app version's
/// wine is currently installed. On every launch we compare it to this app's
/// CFBundleShortVersionString: if the marker is older (or missing while wine is
/// allready installed) we re-run the wine installer to bring the on-disk wine back in
/// sync with the bundled one, then rewrite the marker. Fresh installs are handled by
/// onboarding (which stamps the marker on completion) so the gate wont double-install.
@MainActor
final class WineVersionGate: ObservableObject {
    @Published var updating = false
    @Published var currentStep = ""
    @Published var logLines: [String] = []
    @Published var done = false
    @Published var failed = false

    /// wine components refreshed when the app version moves forward. Each is a real
    /// installer.sh ACTION. install_wine_installer rebuilds the pre-HACK22 installer
    /// overlay so 32-bit installers keep runnin after a wine bump.
    private let wineActions = ["install_wine_unified", "install_wine_installer", "stage_mnc_fonts", "install_dxmt"]

    static var markerPath: String { MacNCheeseSupport.directory + "/wine_version" }

    /// The unified wine loader — if this isnt there yet its a fresh box, so onboarding
    /// (not the gate) owns the first install.
    private static var wineInstalled: Bool {
        FileManager.default.fileExists(atPath: MacNCheeseSupport.directory + "/deps/wine-unified/wine")
    }

    private static var installedVersion: String {
        (try? String(contentsOfFile: markerPath, encoding: .utf8))?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }

    /// True when wine is installed but the marker is missing or older than the app.
    private static var needsUpdate: Bool {
        guard wineInstalled else { return false }
        let installed = installedVersion
        if installed.isEmpty { return true }
        return compareVersions(UpdateChecker.currentVersion, isNewerThan: installed)
    }

    /// Write the running app version into the marker. Called by onboarding after a
    /// first-run install, and by the gate after it finishs an update.
    static func stampInstalled() {
        try? FileManager.default.createDirectory(atPath: MacNCheeseSupport.directory,
                                                 withIntermediateDirectories: true)
        try? UpdateChecker.currentVersion.write(toFile: markerPath, atomically: true, encoding: .utf8)
    }

    /// Fire from the app's launch onAppear. No-op unless a stale wine is detected.
    func check(with backend: BackendClient) {
        guard Self.needsUpdate, !updating else { return }
        updating = true
        currentStep = L("Preparing wine update…")
        logLines = []
        done = false
        failed = false
        Task { await self.run(with: backend) }
    }

    private func run(with backend: BackendClient) async {
        // the backend process is spawned but not connected synchronously; wait for it
        // (mirrors OnboardingView.loadStatus's retry-until-ready) before kickin off the job.
        for _ in 0..<60 {
            if backend.isConnected { break }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        guard let installerPath = InstallerPathStore.installerScriptPath() else {
            finish(fail: true, note: L("installer.sh not found — reinstall MacNCheese."))
            return
        }
        let prefix = backend.activePrefix ?? NSHomeDirectory() + "/wined"
        let p = InstallerPathStore.current()
        guard let jobId = await backend.runInstaller(
            installerPath: installerPath,
            actions: wineActions,
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
            finish(fail: true, note: L("Couldn't start the wine update."))
            return
        }
        // same poll loop as InstallRunner: nil is a transient hiccup, done arrives via progress.done.
        var offset = 0
        var consecutiveFailures = 0
        while true {
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard let progress = await backend.getInstallProgress(jobId: jobId, offset: offset) else {
                consecutiveFailures += 1
                if consecutiveFailures >= 10 {
                    finish(fail: true, note: L("Lost contact with the installer."))
                    return
                }
                continue
            }
            consecutiveFailures = 0
            logLines.append(contentsOf: progress.lines)
            offset = progress.totalLines
            if !progress.current.isEmpty { currentStep = progress.current }
            if progress.done {
                // Stamp the marker as long as the CRITICAL wine is present afterwards. Requiring the
                // WHOLE job to succeed (!progress.failed) meant one non-critical action failing —
                // e.g. install_dxmt's GitHub download timing out — left the marker UNWRITTEN, so the
                // gate re-ran the full wine update on EVERY launch. Now a non-critical failure no
                // longer forces that loop; only a genuinely-missing wine stays unstamped to retry.
                if Self.wineInstalled { Self.stampInstalled() }
                await backend.loadStatus()
                finish(fail: progress.failed, note: progress.failed ? L("Wine update failed.") : "")
                return
            }
        }
    }

    private func finish(fail: Bool, note: String) {
        if !note.isEmpty { logLines.append(note) }
        failed = fail
        done = true
        updating = false
    }

    nonisolated private static func compareVersions(_ a: String, isNewerThan b: String) -> Bool {
        let aParts = a.split(separator: ".").compactMap { Int($0) }
        let bParts = b.split(separator: ".").compactMap { Int($0) }
        let count = max(aParts.count, bParts.count)
        for i in 0..<count {
            let av = i < aParts.count ? aParts[i] : 0
            let bv = i < bParts.count ? bParts[i] : 0
            if av > bv { return true }
            if av < bv { return false }
        }
        return false
    }
}

/// Full-window blocking overlay shown while the gate refreshes wine. Wine cant be used
/// mid-update (games/Steam launch off it), so we block the UI til it finishs — matches
/// the "this runs once per update" expectaton.
struct WineUpdateOverlay: View {
    // passed explicitly (NOT @EnvironmentObject) — overlay content doesnt reliably inherit
    // environmentObjects on macOS 26 SwiftUI, which trapped at first render / launch crash.
    @ObservedObject var wineGate: WineVersionGate

    var body: some View {
        if wineGate.updating {
            ZStack {
                Color.black.opacity(0.9).ignoresSafeArea()
                VStack(spacing: 14) {
                    ProgressView().controlSize(.large).tint(.white)
                    Text(L("Updating wine…"))
                        .font(.title2).fontWeight(.semibold).foregroundStyle(.white)
                    Text(wineGate.currentStep.isEmpty ? L("Working…") : wineGate.currentStep)
                        .font(.callout).foregroundStyle(.white.opacity(0.85))
                        .multilineTextAlignment(.center)
                    // last few installer log lines, updates live as the array grows
                    VStack(alignment: .leading, spacing: 1) {
                        ForEach(Array(wineGate.logLines.suffix(10).enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundStyle(.white.opacity(0.55))
                                .lineLimit(1)
                        }
                    }
                    .frame(width: 460, alignment: .leading)
                    Text(L("Keeping wine in sync with this version. This only runs after an update."))
                        .font(.caption2).foregroundStyle(.white.opacity(0.5))
                        .multilineTextAlignment(.center)
                }
                .padding(30)
            }
            .transition(.opacity)
        }
    }
}
