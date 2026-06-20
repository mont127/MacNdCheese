import SwiftUI
import UniformTypeIdentifiers

struct GameLaunchSheet: View {
    @EnvironmentObject var backend: BackendClient
    @Environment(\.dismiss) private var dismiss
    let game: Game
    var coverImage: NSImage?

    @State private var selectedExe: String = ""
    @State private var detectedExes: [String] = []
    @State private var extraArgs = ""
    @State private var isLaunching = false
    @State private var loadingExes = true
    @State private var selectedBackend: String = "auto"
    @State private var availableBackends: [GraphicsBackend] = []
    @State private var loadingBackends = true
    @State private var retinaMode: Bool = NSScreen.main.map { $0.backingScaleFactor > 1.0 } ?? false
    @State private var metalHud: Bool = false
    @State private var gameMode: Bool = true
    @State private var enableEsync: Bool = true
    @State private var enableMsync: Bool = true
    @State private var advertiseAVX: Bool = false
    @State private var customEnv: String = ""
    @State private var steamMode: String = "silent"
    @State private var steamDescription: String?
    @State private var loadingSteamDescription = false

    private var effectiveExe: String {
        if !selectedExe.isEmpty { return selectedExe }
        return game.exe ?? ""
    }

    var body: some View {
        HStack(alignment: .top, spacing: 20) {
            coverArt

            // Right: header (fixed) + scrollable options + buttons (always visible)
            VStack(alignment: .leading, spacing: 0) {
                header

                ScrollView(.vertical, showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 10) {
                        steamDescriptionSection
                        exeSection
                        graphicsSection
                        argsSection
                        retinaSection
                        metalHudToggle
                        gameModeToggle
                        environmentSection
                        synchronizationSection
                    }
                    .padding(.bottom, 8)
                }

                Divider().padding(.vertical, 8)

                buttonRow
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(24)
        .frame(width: 560, height: 540)
        .background(Color(.windowBackgroundColor))
        .task {
            await loadExes()
            await loadBackends()
            await loadBottleDefaults()   // bottle-level defaults first…
            await loadGameConfig()       // …then per-game overrides
            await loadSteamDescription()
        }
    }

    // MARK: - Sub-views

    private var coverArt: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 14)
                .fill(.ultraThinMaterial)
                .frame(width: 160, height: 240)

            if let image = coverImage {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(width: 160, height: 240)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
            } else {
                Image(systemName: "gamecontroller.fill")
                    .font(.system(size: 40))
                    .foregroundStyle(.secondary)
            }
        }
        .frame(width: 160, height: 240)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(game.name)
                .font(.title2)
                .fontWeight(.bold)
                .lineLimit(2)
            Text("App ID: \(game.appid)")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.bottom, 8)
    }

    @ViewBuilder
    private var steamDescriptionSection: some View {
        if !game.isManual {
            VStack(alignment: .leading, spacing: 6) {
                Text("Steam Description")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fontWeight(.semibold)

                if loadingSteamDescription {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text("Loading from Steam...")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                } else if let steamDescription, !steamDescription.isEmpty {
                    ScrollView {
                        Text(steamDescription)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                            .lineSpacing(2)
                    }
                    .frame(maxHeight: 110)
                    .padding(10)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 10))
                } else {
                    Text("No Steam description available.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 4)
                }
            }
        }
    }

    private var exeSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("EXE:")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fontWeight(.semibold)

            if loadingExes {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Scanning...").font(.caption).foregroundStyle(.secondary)
                }
            } else {
                Picker("", selection: $selectedExe) {
                    Text("Auto-detect").tag("")
                    ForEach(detectedExes, id: \.self) { exe in
                        Text(abbreviateExe(exe)).tag(exe)
                    }
                }
                .labelsHidden()

                Button("Browse…") { browseExe() }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
        }
    }

    private var graphicsSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Graphics Engine:")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fontWeight(.semibold)

            if loadingBackends {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("Detecting...").font(.caption).foregroundStyle(.secondary)
                }
            } else {
                let mainIds: [String] = ["auto", "wine_d3dmetal", "wine_devel", "dxmt", "d3dmetal3", "dxvk", "vkd3d-proton"]
                let experimentalIds: [String] = ["wine", "mesa:llvmpipe", "mesa:zink", "mesa:swr", "gptk", "gptk_full"]
                let mainBackends = availableBackends.filter { mainIds.contains($0.backendId) }
                    .sorted { mainIds.firstIndex(of: $0.backendId) ?? 99 < mainIds.firstIndex(of: $1.backendId) ?? 99 }
                let experimentalBackends = availableBackends.filter { experimentalIds.contains($0.backendId) }
                    .sorted { experimentalIds.firstIndex(of: $0.backendId) ?? 99 < experimentalIds.firstIndex(of: $1.backendId) ?? 99 }

                Picker("", selection: $selectedBackend) {
                    ForEach(mainBackends) { b in
                        Text(engineLabel(b)).tag(b.backendId)
                    }
                    if !experimentalBackends.isEmpty {
                        Divider()
                        Text("— Experimental —").tag("__sep__").disabled(true)
                        ForEach(experimentalBackends) { b in
                            Text(engineLabel(b)).tag(b.backendId)
                        }
                    }
                }
                .labelsHidden()
            }
        }
    }

    private var argsSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Args:")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fontWeight(.semibold)
            TextField("Optional launch arguments…", text: $extraArgs)
                .textFieldStyle(.roundedBorder)
        }
    }

    private var hasRetinaScreen: Bool {
        NSScreen.main.map { $0.backingScaleFactor > 1.0 } ?? false
    }

    private var retinaSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Toggle(isOn: $retinaMode) {
                Text("Retina hi-res mode")
                    .font(.caption)
                    .fontWeight(.semibold)
            }
            .disabled(!hasRetinaScreen)
            Text(hasRetinaScreen
                ? "Default for retina-screen, disable it if you want better performance at the cost of clarity."
                : "Not available — no retina screen detected.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .opacity(hasRetinaScreen ? 1.0 : 0.5)
    }

    private var metalHudToggle: some View {
        Toggle(isOn: $metalHud) {
            Text("Metal HUD")
                .font(.caption)
                .fontWeight(.semibold)
        }
    }

    private var gameModeToggle: some View {
        VStack(alignment: .leading, spacing: 2) {
            Toggle(isOn: $gameMode) {
                Text("Game Mode")
                    .font(.caption)
                    .fontWeight(.semibold)
            }
            Text("Forces macOS Game Mode on while the game runs (prioritizes CPU/GPU and reduces input latency). macOS can't auto-detect Wine games as games, so we enable it for you.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private var environmentSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Environment Variables:")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fontWeight(.semibold)

            Toggle(isOn: $advertiseAVX) {
                Text("Advertise AVX2 / FMA / F16C")
                    .font(.caption)
                    .fontWeight(.semibold)
            }
            Text("Sets ROSETTA_ADVERTISE_AVX=1 so Rosetta exposes AVX2/FMA/F16C. Required by some AAA titles (e.g. God of War Ragnarök). Needs macOS 15+.")
                .font(.caption2)
                .foregroundStyle(.secondary)

            ZStack(alignment: .topLeading) {
                if customEnv.isEmpty {
                    Text("DXVK_ASYNC=1")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.tertiary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 8)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $customEnv)
                    .font(.system(.caption, design: .monospaced))
                    .frame(minHeight: 48, maxHeight: 72)
                    .scrollContentBackground(.hidden)
                    .background(.fill.tertiary)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
            }
            Text("KEY=value, one per line. Saved per game. Combined with the AVX toggle above.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    private var synchronizationSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Synchronization:")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fontWeight(.semibold)

            Toggle(isOn: $enableEsync) {
                Text("Enable ESync")
                    .font(.caption)
                    .fontWeight(.semibold)
            }

            Toggle(isOn: $enableMsync) {
                Text("Enable MSync")
                    .font(.caption)
                    .fontWeight(.semibold)
            }

            Text("MSync is macOS-specific and usually should not be combined with ESync.")
                .font(.caption2)
                .foregroundStyle(.secondary)

            Text("STEAM")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fontWeight(.semibold)
                .padding(.top, 4)

            Picker("", selection: $steamMode) {
                Text("Silent Steam").tag("silent")
                Text("Open Steam").tag("open")
                Text("No Steam").tag("none")
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            Text("Silent: background Steam (no window) — best for Steamworks games like cs2. Open: full Steam UI. No Steam: don't launch Steam — best for standalone UE5/Unity games where background Steam interferes.")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private var buttonRow: some View {
        HStack {
            Button("Cancel") { dismiss() }
                .keyboardShortcut(.cancelAction)

            Spacer()

            Button {
                launchGame()
            } label: {
                HStack(spacing: 6) {
                    if isLaunching {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "play.fill")
                    }
                    Text("Play")
                        .fontWeight(.bold)
                }
                .frame(minWidth: 100)
            }
            .buttonStyle(.borderedProminent)
            .tint(.cyan)
            .controlSize(.large)
            .keyboardShortcut(.defaultAction)
            .disabled((game.epicAppName == nil && effectiveExe.isEmpty) || isLaunching)
        }
    }

    // MARK: - Config load / save

    private func loadGameConfig() async {
        guard let prefix = backend.activePrefix else { return }
        let cfg = await backend.getGameConfig(prefix: prefix, appid: game.appid)
        if let exe = cfg["exe"] as? String, !exe.isEmpty { selectedExe = exe }
        if let b = cfg["backend"] as? String { selectedBackend = b }
        if let a = cfg["args"] as? String { extraArgs = a }
        if let r = cfg["retina_mode"] as? Bool { retinaMode = r }
        if let h = cfg["metal_hud"] as? Bool { metalHud = h }
        if let gm = cfg["game_mode"] as? Bool { gameMode = gm }
        if let e = cfg["esync"] as? Bool { enableEsync = e }
        if let m = cfg["msync"] as? Bool { enableMsync = m }
        if let env = cfg["custom_env"] as? String { customEnv = env }
        if let avx = cfg["rosetta_avx"] as? Bool { advertiseAVX = avx }
        if let sm = cfg["steam_mode"] as? String { steamMode = sm }
    }

    private func saveGameConfig() async {
        guard let prefix = backend.activePrefix else { return }
        let sync = normalizedSyncSelection()
        await backend.setGameConfig(prefix: prefix, appid: game.appid, values: [
            "exe": selectedExe,
            "backend": selectedBackend,
            "args": extraArgs,
            "retina_mode": retinaMode,
            "metal_hud": metalHud,
            "game_mode": gameMode,
            "esync": sync.esync,
            "msync": sync.msync,
            "custom_env": customEnv,
            "rosetta_avx": advertiseAVX,
            "steam_mode": steamMode,
        ])
        // Also remember AVX + custom env at the bottle level (default for new games).
        await backend.setBottleConfig(path: prefix, values: [
            "rosetta_avx": advertiseAVX,
            "custom_env": customEnv,
        ])
    }

    private func loadExes() async {
        loadingExes = true
        detectedExes = await backend.detectExes(installDir: game.installDir)
        if let exe = game.exe, !exe.isEmpty {
            selectedExe = ""  // "Auto-detect" will use game.exe
        }
        loadingExes = false
    }

    private func loadBackends() async {
        loadingBackends = true
        if let response = await backend.listBackends() {
            availableBackends = response.backends.filter { $0.available }
            selectedBackend = "auto"
        }
        loadingBackends = false
    }

    private func loadBottleDefaults() async {
        guard let prefix = backend.activePrefix,
              let config = await backend.getBottleConfig(path: prefix) else { return }
        metalHud = config["metal_hud"] as? Bool ?? false
        gameMode = config["game_mode"] as? Bool ?? true
        advertiseAVX = config["rosetta_avx"] as? Bool ?? false
        customEnv = config["custom_env"] as? String ?? ""
    }

    private func loadSteamDescription() async {
        guard !game.isManual else {
            steamDescription = nil
            loadingSteamDescription = false
            return
        }
        loadingSteamDescription = true
        steamDescription = await backend.getSteamDescription(appid: game.appid)
        loadingSteamDescription = false
    }

    private func normalizedSyncSelection() -> (esync: Bool, msync: Bool) {
        if enableMsync {
            return (false, true)
        }
        return (enableEsync, false)
    }

    /// AVX toggle + custom KEY=VALUE lines folded into the single `custom_env`
    /// string the backend expects (one per line).
    private func combinedCustomEnv() -> String {
        var lines: [String] = []
        if advertiseAVX { lines.append("ROSETTA_ADVERTISE_AVX=1") }
        let extra = customEnv.trimmingCharacters(in: .whitespacesAndNewlines)
        if !extra.isEmpty { lines.append(extra) }
        return lines.joined(separator: "\n")
    }

    private func launchGame() {
        guard let prefix = backend.activePrefix else { return }
        isLaunching = true
        let env = combinedCustomEnv()
        Task {
            await saveGameConfig()
            let sync = normalizedSyncSelection()
            if let appName = game.epicAppName {
                await backend.epicLaunchGame(
                    prefix: prefix,
                    appName: appName,
                    backend: selectedBackend,
                    retinaMode: retinaMode,
                    metalHud: metalHud,
                    gameMode: gameMode,
                    esync: sync.esync,
                    msync: sync.msync,
                    customEnv: env
                )
            } else {
                let exe = effectiveExe
                guard !exe.isEmpty else { isLaunching = false; return }
                await backend.launchGame(
                    prefix: prefix,
                    exe: exe,
                    args: extraArgs,
                    backend: selectedBackend,
                    installDir: game.installDir,
                    retinaMode: retinaMode,
                    metalHud: metalHud,
                    gameMode: gameMode,
                    esync: sync.esync,
                    msync: sync.msync,
                    gameName: game.name,
                    steamAppId: game.appid,
                    steamMode: steamMode,
                    customEnv: env
                )
            }
            isLaunching = false
            dismiss()
        }
    }

    private func browseExe() {
        let panel = NSOpenPanel()
        // Allow .exe and .msi (Windows Installer packages run via msiexec).
        var types: [UTType] = [.exe]
        if let msi = UTType(filenameExtension: "msi") { types.append(msi) }
        panel.allowedContentTypes = types
        panel.canChooseFiles = true
        if !game.installDir.isEmpty {
            panel.directoryURL = URL(fileURLWithPath: game.installDir)
        }
        if panel.runModal() == .OK, let url = panel.url {
            let path = url.path
            if !detectedExes.contains(path) {
                detectedExes.insert(path, at: 0)
            }
            selectedExe = path
        }
    }

    private func engineLabel(_ b: GraphicsBackend) -> String {
        switch b.backendId {
        case "auto":          return "Auto (recommended)"
        case "wine_d3dmetal": return "Wine D3DMetal (CS2 / Source 2 — auto-launches Steam)"
        case "wine_devel":    return "Wine Devel (OpenGL games)"
        case "dxmt":          return "DXMT (Balanced)"
        case "d3dmetal3":     return "D3DMetal (Best Performance)"
        case "dxvk":          return "DXVK (Best Compatibility)"
        case "vkd3d-proton":  return "VKD3D-Proton (D3D12)"
        case "wine":          return "Wine Builtin"
        case "mesa:llvmpipe": return "Mesa llvmpipe (CPU)"
        case "mesa:zink":     return "Mesa Zink (Vulkan)"
        case "mesa:swr":      return "Mesa SWR (CPU/AVX)"
        case "gptk":          return "GPTK (D3DMetal, copy DLLs)"
        case "gptk_full":     return "GPTK Full (Apple Toolkit)"
        default:              return b.label
        }
    }

    private func abbreviateExe(_ path: String) -> String {
        let installDir = game.installDir
        if !installDir.isEmpty, path.hasPrefix(installDir) {
            let relative = String(path.dropFirst(installDir.count))
            return relative.hasPrefix("/") ? String(relative.dropFirst()) : relative
        }
        return URL(fileURLWithPath: path).lastPathComponent
    }
}
