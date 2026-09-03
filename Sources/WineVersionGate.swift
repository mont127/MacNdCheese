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
    /// installer.sh ACTION. The pre-HACK22 installer overlay used to be rebuilt here too;
    /// it is gone — installers run on the unified engine now.
    ///
    /// install_wine_unified is only in the list when deps/ is the engine we are actually
    /// running. Running it while the bundled engine is active would recreate the deps copy
    /// reconcileEngines() has just deleted, and the two would fight on every launch.
    private var wineActions: [String] {
        var actions = ["stage_mnc_fonts", "stage_mnc_tls", "stage_mnc_vulkan",
                       "stage_mnc_sdl", "install_dxmt"]
        if !Self.bundledEngineAvailable { actions.insert("install_wine_unified", at: 0) }
        return actions
    }

    static var markerPath: String { MacNCheeseSupport.directory + "/wine_version" }

    // MARK: - Where the engine lives
    //
    // Two possible homes. The copy in Resources ships inside the .app, so its version IS
    // this app's version. The copy in deps/ is an out-of-band install and records the app
    // version that put it there, in the same wine_version marker the update gate uses.
    //
    // deps/ wins when it is present, because reconcileEngines() only leaves it there when
    // it is strictly newer than what we ship.

    /// <App>.app/Contents/Resources/wine-unified
    static var bundledEnginePath: String {
        (Bundle.main.resourcePath ?? Bundle.main.bundlePath) + "/wine-unified"
    }

    /// ~/Library/Application Support/MacNCheese/deps/wine-unified
    static var depsEnginePath: String {
        MacNCheeseSupport.directory + "/deps/wine-unified"
    }

    /// Keyed on the loader rather than the directory: a half-deleted or half-copied tree
    /// has the directory and nothing that can run.
    private static func enginePresent(at path: String) -> Bool {
        FileManager.default.fileExists(atPath: path + "/loader/wine")
    }

    static var bundledEngineAvailable: Bool { enginePresent(at: bundledEnginePath) }
    static var depsEngineAvailable: Bool { enginePresent(at: depsEnginePath) }

    /// The engine can also ride along as an ARCHIVE rather than an extracted tree --
    /// Resources/wine-unified-bundle.tar.xz, wich is what the nightly DMGs carry. It is
    /// not something we can run from (installer.sh extracts it into deps/), but its
    /// presence does mean this install has an engine available to put on disk.
    static var bundledEngineArchiveAvailable: Bool {
        let res = Bundle.main.resourcePath ?? Bundle.main.bundlePath
        return ["wine-unified-bundle.tar.xz", "wine-unified-bundle.zip"].contains {
            FileManager.default.fileExists(atPath: res + "/" + $0)
        }
    }

    private static var onboardingComplete: Bool {
        UserDefaults.standard.bool(forKey: OnboardingView.completeKey)
    }

    /// The unified wine loader — if neither copy is there yet its a fresh box, so
    /// onboarding (not the gate) owns the first install.
    private static var wineInstalled: Bool {
        // The archive only counts once onboarding is done. Before that this IS a fresh
        // box and onboarding owns the first install, so counting it would have both of
        // them installing the engine at once. After that, an install carrying an archive
        // with no engine on disk is a broken install -- deps/ wiped, an interrupted
        // first run, or an app updated while its engine went missing -- and the gate is
        // the only thing left that can put one back. Leaving it out is how a launch ends
        // up silently running on whatever stray wine the backend can find instead.
        bundledEngineAvailable || depsEngineAvailable
            || (bundledEngineArchiveAvailable && onboardingComplete)
    }

    enum EngineReconcile: String {
        case bundleMissing   = "no bundled engine — deps/ is all we have"
        case bundleOnly      = "running the bundled engine"
        case depsNewer       = "keeping deps/ — it is newer than the bundled engine"
        case depsRemoved     = "removed the redundant deps/ engine"
        case depsRemoveFailed = "could not remove the deps/ engine"
    }

    /// Keep one engine on disk, not two.
    ///
    /// The bundled copy is refreshed by every app update and cannot drift, so it wins on
    /// newer OR equal. deps/ survives only while it is strictly newer — someone dropped a
    /// newer engine in by hand, or a hotfix landed between releases. Otherwise deps/ is a
    /// duplicate of what we already ship, and it is several GB of duplicate.
    ///
    /// A deps/ engine with no marker is the pre-bundling state every existing install is
    /// in. That is the case this migration exists to clean up, so it counts as not-newer
    /// and goes.
    ///
    /// We never delete out of Resources, whichever way the comparison lands. The bundle is
    /// ad-hoc signed and `codesign --verify --deep --strict` runs over it, the app may be
    /// running from a read-only DMG, and the next app update would put the files back — so
    /// the space would not stay reclaimed even where the delete is possible.
    @discardableResult
    static func reconcileEngines() -> EngineReconcile {
        guard bundledEngineAvailable else { return .bundleMissing }
        guard depsEngineAvailable else { return .bundleOnly }

        let deps = installedVersion
        if !deps.isEmpty && compareVersions(deps, isNewerThan: UpdateChecker.currentVersion) {
            return .depsNewer
        }
        do {
            try FileManager.default.removeItem(atPath: depsEnginePath)
            stampInstalled()
            return .depsRemoved
        } catch {
            return .depsRemoveFailed
        }
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

    /// Fire from the app's launch onAppear. Reconciles the two engine copies first, then
    /// no-ops unless a stale wine is detected.
    func check(with backend: BackendClient) {
        let outcome = Self.reconcileEngines()
        if outcome != .bundleOnly { NSLog("MacNCheese: engine — %@", outcome.rawValue) }
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

        if fail {
            let logPath = Self.markerPath + "_error.log"
            let formatter = ISO8601DateFormatter()
            let timestamp = formatter.string(from: Date())
            let header = "--- Wine Update Failed at \(timestamp) ---\n"
            let logContent = header + logLines.joined(separator: "\n") + "\n"

            if let fileHandle = FileHandle(forWritingAtPath: logPath) {
                defer { try? fileHandle.close() }
                fileHandle.seekToEndOfFile()
                if let data = logContent.data(using: .utf8) {
                    fileHandle.write(data)
                }
            } else {
                try? logContent.write(toFile: logPath, atomically: true, encoding: .utf8)
            }
        }
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
