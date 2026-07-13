import AppKit
import Foundation

/// Communicates with the Python backend_server.py via JSON over stdin/stdout.
@MainActor
final class BackendClient: ObservableObject {
    @Published var bottles: [Bottle] = []
    @Published var games: [Game] = []
    @Published var apps: [WineApp] = []
    /// Executables passed to the app via Finder ("Open With") that are waiting
    /// for a bottle to be chosen. Buffered here so a cold launch (where the
    /// file arrives before ContentView exists) isn't lost.
    @Published var pendingExecutables: [URL] = []
    @Published var status: BackendStatus?
    @Published var componentsStatus: ComponentsStatus?
    @Published var isConnected = false
    @Published var activePrefix: String? {
        didSet { UserDefaults.standard.set(activePrefix, forKey: "lastActivePrefix") }
    }
    @Published var runningGamePid: Int?
    @Published var lastError: String?
    /// True while the initial games+apps scan is in flight for the active
    /// Steam/manual bottle. Epic bottles manage their own loading state
    /// internally (EpicLandingView) and never set this.
    @Published var isLoadingLibrary = false
    /// True when the active bottle's folder can't be found on disk right now
    /// (e.g. it lives on an external drive that's since been unmounted).
    @Published var activeBottlePathMissing = false
    /// Bumped on every volume mount/unmount so views that read per-item
    /// on-disk reachability (sidebar bottle rows, game tiles) re-render —
    /// those checks are plain FileManager calls, not @Published state, so
    /// nothing would otherwise tell SwiftUI to re-evaluate them.
    @Published private(set) var volumeChangeTick = 0

    private var process: Process?
    private var stdinPipe: Pipe?
    private var stdoutPipe: Pipe?
    private var requestId = 0
    private var pendingCallbacks: [Int: (Result<Any, Error>) -> Void] = [:]
    private var readBuffer = Data()

    /// Snapshot of the last successful scan per bottle path, persisted to disk
    /// (UserDefaults) so switching to — or cold-launching into — an
    /// already-visited bottle shows its games/apps instantly instead of a
    /// loading spinner, even on the very first scan of a new app launch.
    /// selectBottle still fires a background rescan to keep it fresh.
    /// Reachability is always re-checked fresh on selection BEFORE this cache
    /// is ever consulted, so a stale entry can never mask a bottle that's
    /// since gone missing — worst case it shows last session's games for a
    /// moment before the rescan replaces them.
    private struct LibrarySnapshot: Codable {
        var games: [Game] = []
        var apps: [WineApp] = []
    }
    private static let libraryCacheKey = "BackendClient.libraryCache.v1"
    private var libraryCache: [String: LibrarySnapshot] = BackendClient.loadPersistedLibraryCache() {
        didSet { Self.persistLibraryCache(libraryCache) }
    }

    private static func loadPersistedLibraryCache() -> [String: LibrarySnapshot] {
        guard let data = UserDefaults.standard.data(forKey: libraryCacheKey),
              let decoded = try? JSONDecoder().decode([String: LibrarySnapshot].self, from: data)
        else { return [:] }
        return decoded
    }

    private static func persistLibraryCache(_ cache: [String: LibrarySnapshot]) {
        guard let data = try? JSONEncoder().encode(cache) else { return }
        UserDefaults.standard.set(data, forKey: libraryCacheKey)
    }

    // MARK: - Lifecycle

    func start() {
        let proc = Process()
        let inPipe = Pipe()
        let outPipe = Pipe()
        let errPipe = Pipe()

        // Find backend_server.py relative to the Swift executable or in known locations
        let backendPath = findBackendScript()

        proc.executableURL = URL(fileURLWithPath: findPython())
        proc.arguments = [backendPath]
        proc.standardInput = inPipe
        proc.standardOutput = outPipe
        proc.standardError = errPipe
        proc.currentDirectoryURL = URL(fileURLWithPath: NSString(string: backendPath).deletingLastPathComponent)

        // Read stdout for JSON responses
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            Task { @MainActor [weak self] in
                self?.handleStdoutData(data)
            }
        }

        // Log stderr
        errPipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if let text = String(data: data, encoding: .utf8), !text.isEmpty {
                print("[backend] \(text)", terminator: "")
            }
        }

        proc.terminationHandler = { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.isConnected = false
            }
        }

        do {
            try proc.run()
            self.process = proc
            self.stdinPipe = inPipe
            self.stdoutPipe = outPipe
            self.isConnected = true

            // Initial data load
            Task {
                await refreshAll()
            }
        } catch {
            lastError = String(format: L("Failed to start backend: %@"), error.localizedDescription)
        }

        // React to external drives coming and going for the lifetime of the
        // app, not just at bottle-selection time — otherwise a drive pulled
        // while the user is just browsing an already-loaded library leaves a
        // stale grid on screen (Launch would silently fail), and a drive
        // reconnected while a *different* bottle is active never refreshes
        // that bottle's sidebar icon/dimming until it's re-selected.
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didMountNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in self?.handleVolumeMounted() }
        }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didUnmountNotification, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in self?.handleVolumeUnmounted() }
        }
    }

    /// A drive appeared. If it's the one backing the currently-missing active
    /// bottle, do a full reload. Otherwise, only bother with a silent rescan
    /// if something in the currently-shown library is already known to be
    /// unreachable (e.g. a game symlinked to external storage) — this mount
    /// might be what fixes it. Deliberately NOT unconditional: with an
    /// already-healthy library, re-scanning on every unrelated volume mount
    /// (Time Machine snapshots, random USB sticks, DMG installers) would be
    /// pure waste, and re-triggers the exact slow-scan cost #93 fixed.
    private func handleVolumeMounted() {
        volumeChangeTick += 1
        guard let prefix = activePrefix else { return }
        let reachable = bottles.first { $0.path == prefix }?.isReachable ?? true
        guard reachable else { return }
        if activeBottlePathMissing {
            selectBottle(prefix)
        } else if games.contains(where: { !$0.isReachable }) || apps.contains(where: { !$0.isReachable }) {
            Task {
                await scanGames(prefix: prefix)
                await scanApps(prefix: prefix)
            }
        }
    }

    /// A drive disappeared. If it was backing the active bottle, stop showing
    /// content that's no longer valid instead of leaving a stale grid up.
    private func handleVolumeUnmounted() {
        volumeChangeTick += 1
        guard let prefix = activePrefix, !activeBottlePathMissing else { return }
        let reachable = bottles.first { $0.path == prefix }?.isReachable ?? true
        guard !reachable else { return }
        activeBottlePathMissing = true
        games = []
        apps = []
        isLoadingLibrary = false
    }

    func stop() {
        process?.terminate()
        process = nil
        stdinPipe = nil
        stdoutPipe = nil
        isConnected = false
    }

    // MARK: - Public API

    func refreshAll() async {
        await loadBottles()
        await loadStatus()
        // Normally loadBottles() already restored (and scanned) the last-active
        // bottle via selectBottle(), so activePrefix is nil here. This block is
        // a defensive fallback for a hypothetical future call site that invokes
        // refreshAll() with a bottle already selected.
        if let prefix = activePrefix {
            let reachable = bottles.first { $0.path == prefix }?.isReachable ?? true
            activeBottlePathMissing = !reachable
            guard reachable else { return }
            if let cached = libraryCache[prefix] {
                games = cached.games
                apps = cached.apps
            } else {
                isLoadingLibrary = true
            }
            await scanGames(prefix: prefix)
            await scanApps(prefix: prefix)
            if activePrefix == prefix { isLoadingLibrary = false }
        }
    }

    func loadBottles() async {
        do {
            let result = try await send(cmd: "list_bottles")
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode([Bottle].self, from: data) {
                self.bottles = decoded
                GameIndexCache.updateBottles(decoded)
                // Restore last active bottle, fall back to first
                if activePrefix == nil {
                    let last = UserDefaults.standard.string(forKey: "lastActivePrefix")
                    let match = last.flatMap { l in decoded.first { $0.path == l } }
                    if let bottle = match ?? decoded.first {
                        selectBottle(bottle.path)
                    }
                }
            }
        } catch {
            lastError = String(format: L("Failed to load bottles: %@"), error.localizedDescription)
        }
    }

    func scanGames(prefix: String) async {
        do {
            let result = try await send(cmd: "scan_games", params: ["prefix": prefix])
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode([Game].self, from: data) {
                // Keep the cache warm for this prefix even if the user has since
                // navigated elsewhere — it's still useful the next time they visit.
                libraryCache[prefix, default: LibrarySnapshot()].games = decoded
                // Discard results if the user switched bottles while the request was in flight.
                guard activePrefix == prefix else { return }
                self.games = decoded
                let bottleName = bottles.first { $0.path == prefix }?.name ?? ""
                GameIndexCache.updateGames(decoded, bottlePath: prefix, bottleName: bottleName)
                SpotlightIndexer.index(games: decoded, bottlePath: prefix, bottleName: bottleName)
            }
        } catch {
            lastError = String(format: L("Failed to scan games: %@"), error.localizedDescription)
        }
    }

    func scanApps(prefix: String) async {
        do {
            let result = try await send(cmd: "scan_apps", params: ["prefix": prefix])
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode([WineApp].self, from: data) {
                // Keep the cache warm for this prefix even if the user has since
                // navigated elsewhere — it's still useful the next time they visit.
                libraryCache[prefix, default: LibrarySnapshot()].apps = decoded
                // Discard results if the user switched bottles while in flight.
                guard activePrefix == prefix else { return }
                self.apps = decoded
            }
        } catch {
            // Non-fatal: an old backend without scan_apps just yields no apps.
        }
    }

    func selectBottle(_ path: String) {
        let bottle = bottles.first { $0.path == path }
        activePrefix = path

        // Default to "reachable" if the bottle list hasn't loaded yet, so we
        // don't flash a false "drive missing" state before loadBottles() runs.
        let reachable = bottle?.isReachable ?? true
        activeBottlePathMissing = !reachable
        guard reachable else {
            games = []
            apps = []
            isLoadingLibrary = false
            return  // nothing to scan against a dead path
        }

        // Show what we already know instantly (no spinner) and refresh silently
        // in the background; only fall back to a loading state on a bottle
        // we've genuinely never scanned before.
        if let cached = libraryCache[path] {
            games = cached.games
            apps = cached.apps
            isLoadingLibrary = false
        } else {
            games = []
            apps = []
            isLoadingLibrary = true
        }

        let isEpic = bottle?.isEpicBottle ?? false
        let isAmazon = bottle?.isAmazonBottle ?? false
        Task {
            if isEpic {
                await legendaryStatus()
                await epicCheckAuth()
            }
            if isAmazon {
                await nileStatus()
                await amazonCheckAuth()
            }
            await scanGames(prefix: path)
            await scanApps(prefix: path)
            // Discard if the user switched bottles again while this was in flight.
            if activePrefix == path { isLoadingLibrary = false }
        }
    }

    func launchGame(prefix: String, exe: String, args: String = "", backend: String = "auto", installDir: String = "", retinaMode: Bool = false, metalHud: Bool = false, gameMode: Bool = true, esync: Bool = true, msync: Bool = true, gameName: String = "", steamAppId: String = "", steamMode: String = "silent", customEnv: String = "", debug: Bool = false) async {
        do {
            let screenInfo = NSScreen.screens.map { s in
                "\(s.localizedName): scale=\(s.backingScaleFactor) res=\(Int(s.frame.width))x\(Int(s.frame.height))"
            }.joined(separator: " | ")
            let result = try await send(cmd: "launch_game", params: [
                "prefix": prefix, "exe": exe, "args": args, "backend": backend, "install_dir": installDir,
                "retina_mode": retinaMode, "metal_hud": metalHud, "game_mode": gameMode, "esync": esync, "msync": msync,
                "screen_info": screenInfo, "game_name": gameName, "steam_appid": steamAppId,
                "steam_mode": steamMode, "custom_env": customEnv, "debug": debug,
                "auto_stop_steam": UserDefaults.standard.object(forKey: "auto_stop_steam") as? Bool ?? true,
            ])
            // Backend duplicate-launch guard: the same exe is still alive from a
            // previous launch, so nothing new was spawned. Tell the user what to
            // do instead of silently stacking Wine instances.
            if let dict = result as? [String: Any],
               (dict["already_running"] as? Bool) == true {
                runningGamePid = dict["pid"] as? Int
                lastError = L("This game is already running. If it's frozen, press the red stop button (Kill Wineserver), then launch again.")
                return
            }
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(LaunchResult.self, from: data) {
                runningGamePid = decoded.pid
            }
        } catch {
            lastError = String(format: L("Failed to launch game: %@"), error.localizedDescription)
        }
    }

    @Published var epicAuthenticated = false
    @Published var epicDisplayName: String? = nil
    @Published var legendaryInstalled = false
    @Published var legendaryInstalling = false
    @Published var epicDownloads: [String: EpicDownloadState] = [:]
    @Published var epicAuthURL: URL? = nil

    @Published var amazonAuthenticated = false
    @Published var amazonDisplayName: String? = nil
    @Published var nileInstalled = false
    @Published var nileInstalling = false
    @Published var amazonDownloads: [String: AmazonDownloadState] = [:]

    @Published var steamRunning = false
    /// True while a SteamSetup install is in progress -> ContentView shows the "Installing Steam…"
    /// loading overlay. The SteamSetup GUI wizard doesnt surface under wine so the install runs
    /// SILENT (/S); this is how the user knows its actualy workin. Set by watchSteamInstall.
    @Published var steamInstalling = false
    @Published var steamInstallStep = ""
    private var steamPollTask: Task<Void, Never>?

    func launchLauncher(prefix: String) async {
        let retinaMode = NSScreen.main.map { $0.backingScaleFactor > 1.0 } ?? false
        do {
            let result = try await send(cmd: "launch_launcher", params: [
                "prefix": prefix, "retina_mode": retinaMode
            ])
            if let dict = result as? [String: Any] {
                steamRunning = true
                let _ = dict["already_running"] as? Bool ?? false
            }
        } catch {
            lastError = String(format: L("Failed to launch: %@"), error.localizedDescription)
            return
        }
        startSteamPolling()
        focusWineWindow()
    }

    func launchSteam(prefix: String) async {
        let retinaMode = NSScreen.main.map { $0.backingScaleFactor > 1.0 } ?? false
        do {
            let result = try await send(cmd: "launch_steam", params: [
                "prefix": prefix, "retina_mode": retinaMode,
                "auto_stop_steam": UserDefaults.standard.object(forKey: "auto_stop_steam") as? Bool ?? true,
            ])
            if let dict = result as? [String: Any] {
                steamRunning = true
                let _ = dict["already_running"] as? Bool ?? false
            }
        } catch {
            lastError = String(format: L("Failed to launch Steam: %@"), error.localizedDescription)
            return
        }
        startSteamPolling()
        focusWineWindow()
    }

    /// Show the "Installing Steam…" loading screen + poll steam_install_status until steam.exe lands.
    /// SteamSetup runs silent (/S) becuse its GUI wizard doesnt reliably surface under wine, so
    /// without this the user has no idea an install is happenin. Clears on success, on
    /// ran-then-stopped-without-steam.exe (likely failed), or a ~3min timeout.
    func watchSteamInstall(prefix: String) {
        steamInstalling = true
        steamInstallStep = L("Preparing…")
        Task { @MainActor in
            var sawRunning = false
            var idleAfterRun = 0
            for _ in 0..<120 {   // ~120 * 1.5s = 180s cap
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                let s = (try? await self.send(cmd: "steam_install_status",
                                              params: ["prefix": prefix])) as? [String: Any]
                let installed = (s?["installed"] as? Bool) ?? false
                let running = (s?["running"] as? Bool) ?? false
                if installed {
                    steamInstallStep = L("Steam installed ✓")
                    try? await Task.sleep(nanoseconds: 700_000_000)
                    steamInstalling = false
                    await loadBottles()
                    return
                }
                if running {
                    sawRunning = true; idleAfterRun = 0
                    steamInstallStep = L("Installing Steam…")
                } else if sawRunning {
                    idleAfterRun += 1
                    if idleAfterRun >= 4 { break }   // ran then stopped w/o steam.exe -> give up
                }
            }
            steamInstalling = false
        }
    }

    func startSteamPolling() {
        steamPollTask?.cancel()
        steamPollTask = Task { [weak self] in
            while !Task.isCancelled {
                // 5s is plenty for "did Steam die?" and halves idle wakeups.
                try? await Task.sleep(nanoseconds: 5_000_000_000)
                guard !Task.isCancelled, let self else { break }
                do {
                    let result = try await self.send(cmd: "get_steam_running")
                    if let dict = result as? [String: Any] {
                        let running = dict["running"] as? Bool ?? false
                        self.steamRunning = running
                        if !running { break }
                    }
                } catch {
                    break
                }
            }
        }
    }

    private func focusWineWindow() {
        Task {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            for app in NSWorkspace.shared.runningApplications {
                let exe = app.executableURL?.lastPathComponent ?? ""
                if exe.lowercased().contains("wine") {
                    app.activate()
                    break
                }
            }
        }
    }

    private func pollAndFocusSetup() {
        Task {
            for _ in 0..<20 {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                do {
                    let result = try await send(cmd: "get_setup_pid")
                    if let dict = result as? [String: Any],
                       let running = dict["running"] as? Bool, running {
                        focusWineWindow()
                        return
                    }
                } catch { return }
            }
        }
    }

    // Bradar "auto" isnt a meaningful GLOBAL backend (theres nothing above it to defer
    // to) -- it only makes sense as the per-game override. New bottles need a concrete
    // default so the toolbar's global backend picker never opens on a blank selection.
    func createBottle(name: String, path: String? = nil, launcherType: String = "steam", defaultBackend: String = "d3dmetal3", steamSetupPath: String? = nil) async {
        do {
            var params: [String: Any] = [
                "name": name,
                "launcher_type": launcherType,
                "default_backend": defaultBackend,
            ]
            if let path = path { params["path"] = path }
            if let steamSetupPath { params["steam_setup_path"] = steamSetupPath }
            _ = try await send(cmd: "create_bottle", params: params)
            await loadBottles()
            if launcherType == "steam" {
                pollAndFocusSetup()
                // steam bottles auto-run SteamSetup silently -> show the "Installing Steam…" screen.
                if let p = bottles.first(where: { $0.name == name })?.path {
                    watchSteamInstall(prefix: p)
                }
            }
        } catch {
            lastError = String(format: L("Failed to create bottle: %@"), error.localizedDescription)
        }
    }

    func reorderBottles(paths: [String]) async {
        bottles = paths.compactMap { p in bottles.first { $0.path == p } }
        do {
            _ = try await send(cmd: "reorder_bottles", params: ["paths": paths])
        } catch {
            lastError = String(format: L("Failed to reorder bottles: %@"), error.localizedDescription)
        }
    }

    func deleteBottle(path: String) async {
        do {
            _ = try await send(cmd: "delete_bottle", params: ["path": path])
            SpotlightIndexer.deleteForBottle(path)
            GameIndexCache.removeGames(forBottle: path)
            libraryCache.removeValue(forKey: path)
            if activePrefix == path {
                activePrefix = nil
                games = []
            }
            await loadBottles()
        } catch {
            lastError = String(format: L("Failed to delete bottle: %@"), error.localizedDescription)
        }
    }

    func killWineserver(prefix: String) async {
        do {
            _ = try await send(cmd: "kill_wineserver", params: ["prefix": prefix])
        } catch {
            lastError = String(format: L("Failed to kill wineserver: %@"), error.localizedDescription)
        }
    }

    func initPrefix(prefix: String) async {
        do {
            _ = try await send(cmd: "init_prefix", params: ["prefix": prefix])
        } catch {
            lastError = String(format: L("Failed to init prefix: %@"), error.localizedDescription)
        }
    }

    func cleanPrefix(prefix: String) async {
        do {
            _ = try await send(cmd: "clean_prefix", params: ["prefix": prefix])
        } catch {
            lastError = String(format: L("Failed to clean prefix: %@"), error.localizedDescription)
        }
    }

    /// Launch a discovered Windows application using the bottle's default
    /// graphics backend, the same pipeline games use.
    func launchApp(prefix: String, app: WineApp) async {
        let bottle = bottles.first { $0.path == prefix }
        let backendId = bottle?.defaultBackend ?? "auto"
        let installDir = URL(fileURLWithPath: app.exe).deletingLastPathComponent().path
        await launchGame(
            prefix: prefix,
            exe: app.exe,
            args: app.args,
            backend: backendId,
            installDir: installDir,
            gameName: app.name,
            // Bradar a discovered windows app aint a steam game, so dont drag steam up
            // just to run it. if steam happen to be up already it still see it, we just
            // dont START it (steam_mode none skips the launch, not a running instance)
            steamMode: "none"
        )
    }

    /// Bradar persist a user-picked .exe as a manual app so it shows in the Applications
    /// section (scan_apps merges the bottle's manual_apps). Then re-scan to show it.
    func addManualApp(prefix: String, name: String, exe: String) async {
        do {
            _ = try await send(cmd: "add_manual_app", params: ["prefix": prefix, "name": name, "exe": exe])
        } catch {
            lastError = String(format: L("Failed to add application: %@"), error.localizedDescription)
        }
    }

    func runExe(prefix: String, exe: String, args: String = "") async {
        do {
            _ = try await send(cmd: "run_exe", params: ["prefix": prefix, "exe": exe, "args": args])
            // SteamSetup installs silently (no wizard window under wine) -> show the loading screen.
            if exe.lowercased().hasSuffix("steamsetup.exe") { watchSteamInstall(prefix: prefix) }
        } catch {
            lastError = String(format: L("Failed to run exe: %@"), error.localizedDescription)
        }
    }

    /// Launch the uninstaller for an app. Returns the method used
    /// ("uninstaller" = the app's own uninstaller, "control_panel" = Wine's
    /// Add/Remove Programs fallback), or nil on failure.
    @discardableResult
    func uninstallApp(prefix: String, app: WineApp) async -> String? {
        do {
            let result = try await send(cmd: "uninstall_app", params: ["prefix": prefix, "exe": app.exe])
            return (result as? [String: Any])?["method"] as? String
        } catch {
            lastError = "Failed to uninstall app: \(error.localizedDescription)"
            return nil
        }
    }

    func openPrefixFolder(prefix: String) async {
        do {
            _ = try await send(cmd: "open_prefix_folder", params: ["prefix": prefix])
        } catch {
            lastError = String(format: L("Failed to open folder: %@"), error.localizedDescription)
        }
    }

    func getBottleConfig(path: String) async -> [String: Any]? {
        do {
            let result = try await send(cmd: "get_bottle_config", params: ["path": path])
            return result as? [String: Any]
        } catch {
            lastError = String(format: L("Failed to get bottle config: %@"), error.localizedDescription)
        }
        return nil
    }

    func setBottleConfig(path: String, values: [String: Any]) async {
        var params: [String: Any] = ["path": path]
        for (k, v) in values { params[k] = v }
        do {
            _ = try await send(cmd: "set_bottle_config", params: params)
            await loadBottles()
        } catch {
            lastError = String(format: L("Failed to save config: %@"), error.localizedDescription)
        }
    }



    func setGameOrder(prefix: String, order: [String]) async {
        do {
            _ = try await send(cmd: "set_game_order", params: ["prefix": prefix, "order": order])
        } catch {
            lastError = String(format: L("Failed to save game order: %@"), error.localizedDescription)
        }
    }

    func getSteamDescription(appid: String) async -> String? {
        do {
            let result = try await send(cmd: "get_steam_description", params: ["appid": appid])
            return (result as? [String: Any])?["description"] as? String
        } catch {
            return nil
        }
    }

    /// Steam description + showcase screenshots in one call (powers the game
    /// detail page). Uses the backend's curl-based fetch, so it works on Pythons
    /// whose urllib lacks CA certs.
    func getSteamMedia(appid: String) async -> SteamMedia? {
        do {
            let result = try await send(cmd: "get_steam_media", params: ["appid": appid])
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(SteamMedia.self, from: data) {
                return decoded
            }
        } catch {}
        return nil
    }

    func getGameConfig(prefix: String, appid: String) async -> [String: Any] {
        do {
            let result = try await send(cmd: "get_game_config", params: ["prefix": prefix, "appid": appid])
            return (result as? [String: Any]) ?? [:]
        } catch {
            return [:]
        }
    }

    func setGameConfig(prefix: String, appid: String, values: [String: Any]) async {
        var params: [String: Any] = ["prefix": prefix, "appid": appid]
        for (k, v) in values { params[k] = v }
        do {
            _ = try await send(cmd: "set_game_config", params: params)
        } catch {
            lastError = String(format: L("Failed to save game config: %@"), error.localizedDescription)
        }
    }

    func addManualGame(prefix: String, name: String, exe: String, coverPath: String? = nil) async {
        var params: [String: Any] = ["prefix": prefix, "name": name, "exe": exe]
        if let cover = coverPath { params["cover_path"] = cover }
        do {
            _ = try await send(cmd: "add_manual_game", params: params)
            await scanGames(prefix: prefix)
        } catch {
            lastError = String(format: L("Failed to add game: %@"), error.localizedDescription)
        }
    }

    /// Remove a manually-added (non-Steam) game from the bottle's list only —
    /// the files on disk are left untouched. Re-scans so the grid updates.
    func removeManualGame(prefix: String, exe: String) async {
        do {
            _ = try await send(cmd: "remove_manual_game", params: ["prefix": prefix, "exe": exe])
            await scanGames(prefix: prefix)
        } catch {
            lastError = String(format: L("Failed to remove game: %@"), error.localizedDescription)
        }
    }

    func getComponentsStatus() async -> ComponentsStatus? {
        do {
            let result = try await send(cmd: "get_components_status")
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(ComponentsStatus.self, from: data) {
                self.componentsStatus = decoded
                return decoded
            }
        } catch {
            lastError = String(format: L("Failed to get components status: %@"), error.localizedDescription)
        }
        return nil
    }

    /// Probe which Wine builds are actually installed on disk (with their real
    /// --version strings) so the Bottle tab can show a truthful, detected Wine
    /// selector instead of a hardcoded list.
    func detectWine() async -> WineDetection? {
        do {
            let result = try await send(cmd: "detect_wine")
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(WineDetection.self, from: data) {
                return decoded
            }
        } catch {
            lastError = String(format: L("Failed to detect Wine: %@"), error.localizedDescription)
        }
        return nil
    }

    /// Start the application self-update: download the newest DMG from
    /// mont127/MacNdCheese, extract+codesign the .app, and stage a detached
    /// swapper. Returns a job id; poll getInstallProgress, then quit the app so
    /// the swapper can replace it and relaunch.
    func applyAppUpdate(appPath: String, appPid: Int, dmgURL: String) async -> String? {
        do {
            let result = try await send(cmd: "apply_app_update", params: [
                "app_path": appPath,
                "app_pid": appPid,
                "dmg_url": dmgURL,
            ])
            if let dict = result as? [String: Any], let jobId = dict["job_id"] as? String {
                return jobId
            }
        } catch {
            lastError = String(format: L("Failed to start update: %@"), error.localizedDescription)
        }
        return nil
    }

    func getInstallProgress(jobId: String, offset: Int) async -> InstallProgress? {
        do {
            let result = try await send(cmd: "get_install_progress", params: [
                "job_id": jobId,
                "offset": offset,
            ])
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(InstallProgress.self, from: data) {
                return decoded
            }
        } catch {
            lastError = String(format: L("Failed to get install progress: %@"), error.localizedDescription)
        }
        return nil
    }

    func getUpdateInfo() async -> UpdateInfo? {
        do {
            let result = try await send(cmd: "get_update_info")
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(UpdateInfo.self, from: data) {
                return decoded
            }
        } catch {
            lastError = String(format: L("Failed to get update info: %@"), error.localizedDescription)
        }
        return nil
    }

    func listBackends() async -> BackendsResponse? {
        do {
            let result = try await send(cmd: "list_backends")
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(BackendsResponse.self, from: data) {
                return decoded
            }
        } catch {
            lastError = String(format: L("Failed to list backends: %@"), error.localizedDescription)
        }
        return nil
    }

    // Bradar this one ask the backend how is the microfone and give back the info bradar
    func chekAudioInpit() async -> AudioInpitInfo? {
        do {
            let result = try await send(cmd: "check_audio_input")
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(AudioInpitInfo.self, from: data) {
                return decoded
            }
        } catch { }
        // Bradar what is this comment delet this
        return nil
    }

    // Bradar we tell the backend to open the sound setting so the user change the microfone bradar
    func openSundSetings() async {
        _ = try? await send(cmd: "open_sound_settings")
    }

    func openWinecfg(prefix: String) async {
        do {
            _ = try await send(cmd: "open_winecfg", params: ["prefix": prefix])
        } catch {
            lastError = String(format: L("Failed to open winecfg: %@"), error.localizedDescription)
        }
    }

    func moveBottle(path: String, destinationPath: String) async -> Bool {
        do {
            _ = try await send(cmd: "move_bottle", params: ["path": path, "destination": destinationPath])
            await loadBottles()
            return true
        } catch {
            lastError = String(format: L("Failed to move bottle: %@"), error.localizedDescription)
            return false
        }
    }

    func diagnoseCheese(prefix: String?) async -> CheeseDiagnosis? {
        do {
            var params: [String: Any] = [:]
            if let prefix { params["prefix"] = prefix }
            let result = try await send(cmd: "diagnose_cheese", params: params)
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(CheeseDiagnosis.self, from: data) {
                return decoded
            }
        } catch {
            lastError = String(format: L("Diagnosis failed: %@"), error.localizedDescription)
        }
        return nil
    }

    func runCheeseRepair(action: String, prefix: String?) async -> String? {
        do {
            var params: [String: Any] = ["action": action]
            if let prefix { params["prefix"] = prefix }
            let result = try await send(cmd: "run_cheese_repair", params: params)
            if let dict = result as? [String: Any], let jobId = dict["job_id"] as? String {
                return jobId
            }
        } catch {
            lastError = String(format: L("Repair failed: %@"), error.localizedDescription)
        }
        return nil
    }

    func runInstaller(installerPath: String, actions: [String], prefix: String,
                      dxvkSrc: String, dxvk64: String, dxvk32: String,
                      mesa: String, mesaUrl: String, dxmt: String, vkd3d: String,
                      gptkDir: String) async -> String? {
        do {
            let result = try await send(cmd: "run_installer", params: [
                "installer_path": installerPath,
                "actions": actions,
                "prefix": prefix,
                "dxvk_src": dxvkSrc,
                "dxvk64": dxvk64,
                "dxvk32": dxvk32,
                "mesa": mesa,
                "mesa_url": mesaUrl,
                "dxmt": dxmt,
                "vkd3d": vkd3d,
                "gptk_dir": gptkDir,
            ])
            if let dict = result as? [String: Any], let jobId = dict["job_id"] as? String {
                return jobId
            }
        } catch {
            lastError = String(format: L("Failed to start installer: %@"), error.localizedDescription)
        }
        return nil
    }

    /// Whether the bundled winetricks binary is present (installed as part of
    /// the normal Setup/portable-tools flow). Gates the Winetricks App Store UI.
    func winetricksAvailable() -> Bool {
        componentsStatus?.hasWinetricks ?? false
    }

    func runWinetricks(prefix: String, verbs: [String], force: Bool = false) async -> String? {
        do {
            let result = try await send(cmd: "winetricks_run", params: [
                "prefix": prefix,
                "verbs": verbs,
                "force": force,
            ])
            if let dict = result as? [String: Any], let jobId = dict["job_id"] as? String {
                return jobId
            }
        } catch {
            lastError = String(format: L("Failed to start install: %@"), error.localizedDescription)
        }
        return nil
    }

    func winetricksCancel(jobId: String) async {
        _ = try? await send(cmd: "winetricks_cancel", params: ["job_id": jobId])
    }

    func winetricksListInstalled(prefix: String) async -> Set<String> {
        do {
            let result = try await send(cmd: "winetricks_list_installed", params: ["prefix": prefix])
            if let dict = result as? [String: Any], let arr = dict["installed"] as? [String] {
                return Set(arr)
            }
        } catch {
            // Non-fatal — the store sheet just shows everything as not-yet-installed.
        }
        return []
    }

    /// The full winetricks verb catalog, parsed by the backend straight from
    /// the bundled winetricks script's own metadata (not a static Swift list).
    func getWinetricksCatalog() async -> [WinetricksVerb] {
        do {
            let result = try await send(cmd: "winetricks_catalog")
            if let dict = result as? [String: Any],
               let data = try? JSONSerialization.data(withJSONObject: dict["verbs"] ?? []),
               let decoded = try? JSONDecoder().decode([WinetricksVerb].self, from: data) {
                return decoded
            }
        } catch {
            lastError = String(format: L("Failed to load the winetricks catalog: %@"), error.localizedDescription)
        }
        return []
    }

    func getExeIcon(exe: String) async -> Data? {
        do {
            let result = try await send(cmd: "get_exe_icon", params: ["exe": exe])
            if let dict = result as? [String: Any],
               let b64 = dict["icon"] as? String,
               let data = Data(base64Encoded: b64) {
                return data
            }
        } catch {}
        return nil
    }

    func detectExes(installDir: String) async -> [String] {
        do {
            let result = try await send(cmd: "detect_exes", params: ["install_dir": installDir])
            if let arr = result as? [String] { return arr }
        } catch {
            lastError = String(format: L("Failed to detect exes: %@"), error.localizedDescription)
        }
        return []
    }

    // MARK: - Epic Games / Legendary

    func legendaryStatus() async {
        do {
            let result = try await send(cmd: "legendary_status")
            if let dict = result as? [String: Any] {
                legendaryInstalled = dict["installed"] as? Bool ?? false
                legendaryInstalling = dict["installing"] as? Bool ?? false
            }
        } catch {}
        if epicAuthURL == nil {
            do {
                let result = try await send(cmd: "legendary_get_auth_url")
                if let dict = result as? [String: Any],
                   let urlStr = dict["url"] as? String,
                   let url = URL(string: urlStr) {
                    epicAuthURL = url
                }
            } catch {}
        }
    }

    func epicCheckAuth() async {
        guard let prefix = activePrefix else { return }
        do {
            let result = try await send(cmd: "legendary_check_auth", params: ["prefix": prefix])
            if let dict = result as? [String: Any] {
                epicAuthenticated = dict["authenticated"] as? Bool ?? false
                epicDisplayName = dict["display_name"] as? String
            }
        } catch {}
    }

    func epicAuth(code: String) async -> (ok: Bool, displayName: String, error: String) {
        guard let prefix = activePrefix else { return (false, "", "No active bottle") }
        do {
            let result = try await send(cmd: "legendary_auth", params: ["code": code, "prefix": prefix])
            if let dict = result as? [String: Any] {
                return (
                    dict["ok"] as? Bool ?? false,
                    dict["display_name"] as? String ?? "",
                    dict["error"] as? String ?? ""
                )
            }
        } catch {
            return (false, "", error.localizedDescription)
        }
        return (false, "", "Unknown error")
    }

    // MARK: - Amazon Games / Nile

    func nileStatus() async {
        do {
            let result = try await send(cmd: "nile_status")
            if let dict = result as? [String: Any] {
                nileInstalled = dict["installed"] as? Bool ?? false
                nileInstalling = dict["installing"] as? Bool ?? false
            }
        } catch {}
    }

    /// Starts a fresh Amazon sign-in attempt. Unlike Epic's `epicAuthURL`, this
    /// is not cached — each attempt needs a new PKCE challenge from the backend,
    /// so callers should invoke this every time the sign-in sheet is opened.
    func nileGetAuthParams() async -> (url: URL?, clientId: String, codeVerifier: String, serial: String) {
        do {
            let result = try await send(cmd: "nile_get_auth_params")
            if let dict = result as? [String: Any],
               let urlStr = dict["url"] as? String,
               let url = URL(string: urlStr) {
                return (
                    url,
                    dict["client_id"] as? String ?? "",
                    dict["code_verifier"] as? String ?? "",
                    dict["serial"] as? String ?? ""
                )
            }
        } catch {}
        return (nil, "", "", "")
    }

    func amazonCheckAuth() async {
        guard let prefix = activePrefix else { return }
        do {
            let result = try await send(cmd: "nile_check_auth", params: ["prefix": prefix])
            if let dict = result as? [String: Any] {
                amazonAuthenticated = dict["authenticated"] as? Bool ?? false
                amazonDisplayName = dict["display_name"] as? String
            }
        } catch {}
    }

    func amazonAuth(code: String, clientId: String, codeVerifier: String, serial: String) async -> (ok: Bool, displayName: String, error: String) {
        guard let prefix = activePrefix else { return (false, "", "No active bottle") }
        do {
            let result = try await send(cmd: "nile_auth", params: [
                "code": code, "prefix": prefix, "client_id": clientId,
                "code_verifier": codeVerifier, "serial": serial,
            ])
            if let dict = result as? [String: Any] {
                return (
                    dict["ok"] as? Bool ?? false,
                    dict["display_name"] as? String ?? "",
                    dict["error"] as? String ?? ""
                )
            }
        } catch {
            return (false, "", error.localizedDescription)
        }
        return (false, "", "Unknown error")
    }

    func epicInstallGame(prefix: String, appName: String) async -> Bool {
        do {
            _ = try await send(cmd: "legendary_install_game", params: [
                "prefix": prefix, "app_name": appName
            ])
            return true
        } catch {
            lastError = String(format: L("Failed to queue install: %@"), error.localizedDescription)
            return false
        }
    }

    func epicInstallProgress(appName: String) async -> (progress: Double, done: Bool, error: String?)? {
        do {
            let result = try await send(cmd: "legendary_install_progress", params: ["app_name": appName])
            if let dict = result as? [String: Any] {
                return (
                    dict["progress"] as? Double ?? 0,
                    dict["done"] as? Bool ?? false,
                    dict["error"] as? String
                )
            }
        } catch {}
        return nil
    }

    func epicCancelInstall(appName: String) async {
        do {
            _ = try await send(cmd: "legendary_cancel_install", params: ["app_name": appName])
        } catch {}
    }

    func refreshEpicDownloads() async {
        do {
            let result = try await send(cmd: "legendary_all_downloads", params: [:])
            guard let dict = result as? [String: Any] else { return }
            var downloads: [String: EpicDownloadState] = [:]
            for (appName, info) in dict {
                guard let info = info as? [String: Any] else { continue }
                downloads[appName] = EpicDownloadState(
                    progress: info["progress"] as? Double ?? 0,
                    queued: info["queued"] as? Bool ?? false,
                    queuePosition: info["queue_position"] as? Int ?? 0,
                    paused: info["paused"] as? Bool ?? false,
                    prefix: info["prefix"] as? String ?? ""
                )
            }
            epicDownloads = downloads
        } catch {}
    }

    func epicPauseInstall(appName: String) async {
        do { _ = try await send(cmd: "legendary_pause_install", params: ["app_name": appName]) } catch {}
    }

    func epicResumeInstall(appName: String) async {
        do { _ = try await send(cmd: "legendary_resume_install", params: ["app_name": appName]) } catch {}
    }

    func amazonInstallGame(prefix: String, amazonId: String) async -> Bool {
        do {
            _ = try await send(cmd: "nile_install_game", params: [
                "prefix": prefix, "amazon_id": amazonId
            ])
            return true
        } catch {
            lastError = String(format: L("Failed to queue install: %@"), error.localizedDescription)
            return false
        }
    }

    func amazonInstallProgress(amazonId: String) async -> (progress: Double, done: Bool, error: String?)? {
        do {
            let result = try await send(cmd: "nile_install_progress", params: ["amazon_id": amazonId])
            if let dict = result as? [String: Any] {
                return (
                    dict["progress"] as? Double ?? 0,
                    dict["done"] as? Bool ?? false,
                    dict["error"] as? String
                )
            }
        } catch {}
        return nil
    }

    func amazonCancelInstall(amazonId: String) async {
        do {
            _ = try await send(cmd: "nile_cancel_install", params: ["amazon_id": amazonId])
        } catch {}
    }

    func refreshAmazonDownloads() async {
        do {
            let result = try await send(cmd: "nile_all_downloads", params: [:])
            guard let dict = result as? [String: Any] else { return }
            var downloads: [String: AmazonDownloadState] = [:]
            for (amazonId, info) in dict {
                guard let info = info as? [String: Any] else { continue }
                downloads[amazonId] = AmazonDownloadState(
                    progress: info["progress"] as? Double ?? 0,
                    queued: info["queued"] as? Bool ?? false,
                    queuePosition: info["queue_position"] as? Int ?? 0,
                    paused: info["paused"] as? Bool ?? false,
                    prefix: info["prefix"] as? String ?? ""
                )
            }
            amazonDownloads = downloads
        } catch {}
    }

    func amazonPauseInstall(amazonId: String) async {
        do { _ = try await send(cmd: "nile_pause_install", params: ["amazon_id": amazonId]) } catch {}
    }

    func amazonResumeInstall(amazonId: String) async {
        do { _ = try await send(cmd: "nile_resume_install", params: ["amazon_id": amazonId]) } catch {}
    }

    func getGameOrder(prefix: String) async -> [String] {
        do {
            let result = try await send(cmd: "get_game_order", params: ["prefix": prefix])
            return (result as? [String]) ?? []
        } catch { return [] }
    }

    func epicLaunchGame(
        prefix: String,
        appName: String,
        backend: String = "auto",
        retinaMode: Bool = false,
        metalHud: Bool = false,
        gameMode: Bool = true,
        esync: Bool = true,
        msync: Bool = true,
        customEnv: String = "",
        debug: Bool = false
    ) async {
        do {
            _ = try await send(cmd: "legendary_launch_game", params: [
                "app_name": appName,
                "prefix": prefix,
                "backend": backend,
                "retina_mode": retinaMode,
                "metal_hud": metalHud,
                "game_mode": gameMode,
                "esync": esync,
                "msync": msync,
                "custom_env": customEnv,
                "debug": debug,
            ])
        } catch {
            lastError = String(format: L("Failed to launch %@: %@"), appName, error.localizedDescription)
        }
    }

    func amazonLaunchGame(
        prefix: String,
        amazonId: String,
        backend: String = "auto",
        retinaMode: Bool = false,
        metalHud: Bool = false,
        gameMode: Bool = true,
        esync: Bool = true,
        msync: Bool = true,
        customEnv: String = "",
        debug: Bool = false
    ) async {
        do {
            _ = try await send(cmd: "nile_launch_game", params: [
                "amazon_id": amazonId,
                "prefix": prefix,
                "backend": backend,
                "retina_mode": retinaMode,
                "metal_hud": metalHud,
                "game_mode": gameMode,
                "esync": esync,
                "msync": msync,
                "custom_env": customEnv,
                "debug": debug,
            ])
        } catch {
            lastError = String(format: L("Failed to launch %@: %@"), amazonId, error.localizedDescription)
        }
    }

    func loadStatus() async {
        do {
            let result = try await send(cmd: "get_status")
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(BackendStatus.self, from: data) {
                self.status = decoded
            }
        } catch {
            lastError = String(format: L("Failed to get status: %@"), error.localizedDescription)
        }
    }

    // MARK: - JSON-RPC Transport

    private func send(cmd: String, params: [String: Any] = [:]) async throws -> Any {
        requestId += 1
        let id = requestId

        var payload = params
        payload["id"] = id
        payload["cmd"] = cmd

        return try await withCheckedThrowingContinuation { continuation in
            pendingCallbacks[id] = { result in
                switch result {
                case .success(let value): continuation.resume(returning: value)
                case .failure(let error): continuation.resume(throwing: error)
                }
            }

            do {
                let data = try JSONSerialization.data(withJSONObject: payload)
                guard let pipe = stdinPipe else {
                    continuation.resume(throwing: BackendError.notConnected)
                    pendingCallbacks.removeValue(forKey: id)
                    return
                }
                var line = data
                line.append(0x0A) // newline
                pipe.fileHandleForWriting.write(line)
            } catch {
                pendingCallbacks.removeValue(forKey: id)
                continuation.resume(throwing: error)
            }
        }
    }

    private func handleStdoutData(_ data: Data) {
        readBuffer.append(data)

        // Process complete lines
        while let newlineIndex = readBuffer.firstIndex(of: 0x0A) {
            let lineData = readBuffer[readBuffer.startIndex..<newlineIndex]
            readBuffer = Data(readBuffer[readBuffer.index(after: newlineIndex)...])

            guard !lineData.isEmpty,
                  let json = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any] else {
                continue
            }

            let id = json["id"] as? Int ?? 0
            let ok = json["ok"] as? Bool ?? false

            if let callback = pendingCallbacks.removeValue(forKey: id) {
                if ok {
                    callback(.success(json["data"] ?? NSNull()))
                } else {
                    let msg = json["error"] as? String ?? "Unknown error"
                    callback(.failure(BackendError.backendError(msg)))
                }
            }
        }
    }

    // MARK: - Helpers

    private func findPython() -> String {
        // Try the project venv first (next to backend_server.py)
        let backendDir = NSString(string: findBackendScript()).deletingLastPathComponent
        let venvPython = backendDir + "/.venv/bin/python3"
        if FileManager.default.fileExists(atPath: venvPython) {
            return venvPython
        }
        let venvPython2 = backendDir + "/.venv/bin/python"
        if FileManager.default.fileExists(atPath: venvPython2) {
            return venvPython2
        }
        // Try venv next to the source repo
        let home = NSHomeDirectory()
        let repoVenv = home + "/macndcheese/.venv/bin/python3"
        if FileManager.default.fileExists(atPath: repoVenv) {
            return repoVenv
        }
        // Try common system locations
        for c in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3",
                   "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"] {
            if FileManager.default.fileExists(atPath: c) { return c }
        }
        return "/usr/bin/python3"
    }

    private func findBackendScript() -> String {
        let home = NSHomeDirectory()
        let resourcePath = Bundle.main.resourcePath ?? Bundle.main.bundlePath
        let candidates = [
            resourcePath + "/backend_server.py",
            "\(home)/macndcheese/backend_server.py",
            Bundle.main.bundlePath + "/../backend_server.py",
            Bundle.main.bundlePath + "/../../backend_server.py",
        ]
        for c in candidates {
            if FileManager.default.fileExists(atPath: c) { return c }
        }
        return candidates[0]
    }
}

enum BackendError: LocalizedError {
    case notConnected
    case backendError(String)

    var errorDescription: String? {
        switch self {
        case .notConnected: return "Backend not connected"
        case .backendError(let msg): return msg
        }
    }
}
