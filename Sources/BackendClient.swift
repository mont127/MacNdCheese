import AppKit
import Foundation

/// Communicates with the Python backend_server.py via JSON over stdin/stdout.
@MainActor
final class BackendClient: ObservableObject {
    @Published var bottles: [Bottle] = []
    @Published var games: [Game] = []
    @Published var status: BackendStatus?
    @Published var componentsStatus: ComponentsStatus?
    @Published var isConnected = false
    @Published var activePrefix: String? {
        didSet { UserDefaults.standard.set(activePrefix, forKey: "lastActivePrefix") }
    }
    @Published var runningGamePid: Int?
    @Published var lastError: String?

    private var process: Process?
    private var stdinPipe: Pipe?
    private var stdoutPipe: Pipe?
    private var requestId = 0
    private var pendingCallbacks: [Int: (Result<Any, Error>) -> Void] = [:]
    private var readBuffer = Data()

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
            lastError = "Failed to start backend: \(error.localizedDescription)"
        }
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
        if let prefix = activePrefix {
            await scanGames(prefix: prefix)
        }
    }

    func loadBottles() async {
        do {
            let result = try await send(cmd: "list_bottles")
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode([Bottle].self, from: data) {
                self.bottles = decoded
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
            lastError = "Failed to load bottles: \(error.localizedDescription)"
        }
    }

    func scanGames(prefix: String) async {
        do {
            let result = try await send(cmd: "scan_games", params: ["prefix": prefix])
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode([Game].self, from: data) {
                self.games = decoded
            }
        } catch {
            lastError = "Failed to scan games: \(error.localizedDescription)"
        }
    }

    func selectBottle(_ path: String) {
        activePrefix = path
        games = []  // clear immediately so stale games don't show for the new bottle
        Task {
            await scanGames(prefix: path)
        }
    }

    func launchGame(prefix: String, exe: String, args: String = "", backend: String = "auto", installDir: String = "", retinaMode: Bool = false, metalHud: Bool = false, esync: Bool = true, msync: Bool = true, gameName: String = "", steamAppId: String = "") async {
        do {
            let screenInfo = NSScreen.screens.map { s in
                "\(s.localizedName): scale=\(s.backingScaleFactor) res=\(Int(s.frame.width))x\(Int(s.frame.height))"
            }.joined(separator: " | ")
            let result = try await send(cmd: "launch_game", params: [
                "prefix": prefix, "exe": exe, "args": args, "backend": backend, "install_dir": installDir,
                "retina_mode": retinaMode, "metal_hud": metalHud, "esync": esync, "msync": msync,
                "screen_info": screenInfo, "game_name": gameName, "steam_appid": steamAppId,
            ])
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(LaunchResult.self, from: data) {
                runningGamePid = decoded.pid
            }
        } catch {
            lastError = "Failed to launch game: \(error.localizedDescription)"
        }
    }

    @Published var steamRunning = false
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
            lastError = "Failed to launch: \(error.localizedDescription)"
            return
        }
        startSteamPolling()
        focusWineWindow()
    }

    func launchSteam(prefix: String) async {
        let retinaMode = NSScreen.main.map { $0.backingScaleFactor > 1.0 } ?? false
        do {
            let result = try await send(cmd: "launch_steam", params: [
                "prefix": prefix, "retina_mode": retinaMode
            ])
            if let dict = result as? [String: Any] {
                steamRunning = true
                let _ = dict["already_running"] as? Bool ?? false
            }
        } catch {
            lastError = "Failed to launch Steam: \(error.localizedDescription)"
            return
        }
        startSteamPolling()
        focusWineWindow()
    }

    func startSteamPolling() {
        steamPollTask?.cancel()
        steamPollTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
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

    func createBottle(name: String, path: String? = nil, launcherType: String = "steam", defaultBackend: String = "auto") async {
        do {
            var params: [String: Any] = [
                "name": name,
                "launcher_type": launcherType,
                "default_backend": defaultBackend,
            ]
            if let path = path { params["path"] = path }
            _ = try await send(cmd: "create_bottle", params: params)
            await loadBottles()
            if launcherType == "steam" {
                pollAndFocusSetup()
            }
        } catch {
            lastError = "Failed to create bottle: \(error.localizedDescription)"
        }
    }

    func reorderBottles(paths: [String]) async {
        bottles = paths.compactMap { p in bottles.first { $0.path == p } }
        do {
            _ = try await send(cmd: "reorder_bottles", params: ["paths": paths])
        } catch {
            lastError = "Failed to reorder bottles: \(error.localizedDescription)"
        }
    }

    func deleteBottle(path: String) async {
        do {
            _ = try await send(cmd: "delete_bottle", params: ["path": path])
            if activePrefix == path {
                activePrefix = nil
                games = []
            }
            await loadBottles()
        } catch {
            lastError = "Failed to delete bottle: \(error.localizedDescription)"
        }
    }

    func killWineserver(prefix: String) async {
        do {
            _ = try await send(cmd: "kill_wineserver", params: ["prefix": prefix])
        } catch {
            lastError = "Failed to kill wineserver: \(error.localizedDescription)"
        }
    }

    func initPrefix(prefix: String) async {
        do {
            _ = try await send(cmd: "init_prefix", params: ["prefix": prefix])
        } catch {
            lastError = "Failed to init prefix: \(error.localizedDescription)"
        }
    }

    func cleanPrefix(prefix: String) async {
        do {
            _ = try await send(cmd: "clean_prefix", params: ["prefix": prefix])
        } catch {
            lastError = "Failed to clean prefix: \(error.localizedDescription)"
        }
    }

    func runExe(prefix: String, exe: String, args: String = "") async {
        do {
            _ = try await send(cmd: "run_exe", params: ["prefix": prefix, "exe": exe, "args": args])
        } catch {
            lastError = "Failed to run exe: \(error.localizedDescription)"
        }
    }

    func openPrefixFolder(prefix: String) async {
        do {
            _ = try await send(cmd: "open_prefix_folder", params: ["prefix": prefix])
        } catch {
            lastError = "Failed to open folder: \(error.localizedDescription)"
        }
    }

<<<<<<< discord-rpc
=======
    func getBottleConfig(path: String) async -> [String: Any]? {
        do {
            let result = try await send(cmd: "get_bottle_config", params: ["path": path])
            return result as? [String: Any]
        } catch {
            lastError = "Failed to get bottle config: \(error.localizedDescription)"
        }
        return nil
    }

>>>>>>> main
    func setBottleConfig(path: String, values: [String: Any]) async {
        var params: [String: Any] = ["path": path]
        for (k, v) in values { params[k] = v }
        do {
            _ = try await send(cmd: "set_bottle_config", params: params)
            await loadBottles()
        } catch {
            lastError = "Failed to save config: \(error.localizedDescription)"
        }
    }

    func addManualGame(prefix: String, name: String, exe: String, coverPath: String? = nil) async {
        var params: [String: Any] = ["prefix": prefix, "name": name, "exe": exe]
        if let cover = coverPath { params["cover_path"] = cover }
        do {
            _ = try await send(cmd: "add_manual_game", params: params)
            await scanGames(prefix: prefix)
        } catch {
            lastError = "Failed to add game: \(error.localizedDescription)"
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
            lastError = "Failed to get components status: \(error.localizedDescription)"
        }
        return nil
    }

    func runInstaller(installerPath: String, actions: [String], prefix: String,
                      dxvkSrc: String, dxvk64: String, dxvk32: String,
                      mesa: String, mesaUrl: String, dxmt: String, vkd3d: String) async -> String? {
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
            ])
            if let dict = result as? [String: Any], let jobId = dict["job_id"] as? String {
                return jobId
            }
        } catch {
            lastError = "Failed to start installer: \(error.localizedDescription)"
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
            lastError = "Failed to get install progress: \(error.localizedDescription)"
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
            lastError = "Failed to get update info: \(error.localizedDescription)"
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
            lastError = "Failed to list backends: \(error.localizedDescription)"
        }
        return nil
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
            lastError = "Failed to detect exes: \(error.localizedDescription)"
        }
        return []
    }

    func loadStatus() async {
        do {
            let result = try await send(cmd: "get_status")
            if let data = try? JSONSerialization.data(withJSONObject: result),
               let decoded = try? JSONDecoder().decode(BackendStatus.self, from: data) {
                self.status = decoded
            }
        } catch {
            lastError = "Failed to get status: \(error.localizedDescription)"
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
