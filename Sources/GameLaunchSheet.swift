import SwiftUI

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
    @State private var enableEsync: Bool = true
    @State private var enableMsync: Bool = true
    @State private var customEnv: String = ""

    private var effectiveExe: String {
        if !selectedExe.isEmpty { return selectedExe }
        return game.exe ?? ""
    }

    var body: some View {
        HStack(alignment: .top, spacing: 20) {
            // Left: Cover art
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

            // Right: Game info + options
            VStack(alignment: .leading, spacing: 0) {
                Text(game.name)
                    .font(.title2)
                    .fontWeight(.bold)
                    .lineLimit(2)
                    .padding(.bottom, 2)

                Text("App ID: \(game.appid)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.bottom, 8)

                // Scrollable options
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 10) {
                        // EXE picker
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
                                        Text(abbreviateExe(exe))
                                            .tag(exe)
                                    }
                                }
                                .labelsHidden()

                                Button("Browse…") { browseExe() }
                                    .buttonStyle(.bordered)
                                    .controlSize(.small)
                            }
                        }

                        // Graphics engine picker
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
                                let mainIds: [String] = ["auto", "dxmt", "d3dmetal3", "dxvk", "vkd3d-proton"]
                                let experimentalIds: [String] = ["wine", "mesa:llvmpipe", "mesa:zink", "mesa:swr", "gptk", "gptk_full"]
                                let mainBackends = availableBackends.filter { mainIds.contains($0.backendId) }
                                    .sorted { mainIds.firstIndex(of: $0.backendId) ?? 99 < mainIds.firstIndex(of: $1.backendId) ?? 99 }
                                let experimentalBackends = availableBackends.filter { experimentalIds.contains($0.backendId) }
                                    .sorted { experimentalIds.firstIndex(of: $0.backendId) ?? 99 < experimentalIds.firstIndex(of: $1.backendId) ?? 99 }

                                Picker("", selection: $selectedBackend) {
                                    ForEach(mainBackends) { b in
                                        Text(engineLabel(b))
                                            .tag(b.backendId)
                                    }
                                    if !experimentalBackends.isEmpty {
                                        Divider()
                                        Text("— Experimental —").tag("__sep__").disabled(true)
                                        ForEach(experimentalBackends) { b in
                                            Text(engineLabel(b))
                                                .tag(b.backendId)
                                        }
                                    }
                                }
                                .labelsHidden()
                            }
                        }

                        // Args
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Args:")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                                .fontWeight(.semibold)
                            TextField("Optional launch arguments…", text: $extraArgs)
                                .textFieldStyle(.roundedBorder)
                        }

                        // Retina mode
                        VStack(alignment: .leading, spacing: 4) {
                            Toggle(isOn: $retinaMode) {
                                Text("Retina hi-res mode")
                                    .font(.caption)
                                    .fontWeight(.semibold)
                            }
                            Text("Enable high resolution for retina screens. Game compatibility might be affected.")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }

                        // Metal HUD
                        Toggle(isOn: $metalHud) {
                            Text("Metal HUD")
                                .font(.caption)
                                .fontWeight(.semibold)
                        }

                        // Synchronization
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
                        }

                        // Custom env vars
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Env Vars:")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                                .fontWeight(.semibold)
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
                            Text("KEY=value, one per line. Saved per game.")
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                    }
                    .padding(.bottom, 8)
                }

                Divider().padding(.vertical, 8)

                // Buttons — always visible, outside the scroll area
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
                    .controlSize(.large)
                    .keyboardShortcut(.defaultAction)
                    .disabled((game.epicAppName == nil && effectiveExe.isEmpty) || isLaunching)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(24)
        .frame(minWidth: 480, minHeight: 400)
        .background(Color(.windowBackgroundColor))
        .task {
            await loadExes()
            await loadBackends()
            await loadBottleDefaults()
            await loadGameConfig()
        }
    }

    private func loadGameConfig() async {
        guard let prefix = backend.activePrefix else { return }
        let cfg = await backend.getGameConfig(prefix: prefix, appid: game.appid)
        if let exe = cfg["exe"] as? String, !exe.isEmpty { selectedExe = exe }
        if let b = cfg["backend"] as? String { selectedBackend = b }
        if let a = cfg["args"] as? String { extraArgs = a }
        if let r = cfg["retina_mode"] as? Bool { retinaMode = r }
        if let h = cfg["metal_hud"] as? Bool { metalHud = h }
        if let e = cfg["esync"] as? Bool { enableEsync = e }
        if let m = cfg["msync"] as? Bool { enableMsync = m }
        if let env = cfg["custom_env"] as? String { customEnv = env }
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
            "esync": sync.esync,
            "msync": sync.msync,
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
    }

    private func normalizedSyncSelection() -> (esync: Bool, msync: Bool) {
        if enableMsync {
            return (false, true)
        }
        return (enableEsync, false)
    }

    private func launchGame() {
        guard let prefix = backend.activePrefix else { return }
        isLaunching = true
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
                    esync: sync.esync,
                    msync: sync.msync,
                    customEnv: customEnv
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
                    esync: sync.esync,
                    msync: sync.msync,
                    gameName: game.name,
                    steamAppId: game.appid,
                    customEnv: customEnv
                )
            }
            isLaunching = false
            dismiss()
        }
    }

    private func browseExe() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.exe]
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
        case "auto":       return "Auto (recommended)"
        case "dxmt":       return "DXMT (Balanced)"
        case "d3dmetal3":  return "D3DMetal (Best Performance)"
        case "dxvk":       return "DXVK (Best Compatibility)"
        case "vkd3d-proton": return "VKD3D-Proton (D3D12)"
        case "wine":       return "Wine Builtin"
        case "mesa:llvmpipe": return "Mesa llvmpipe (CPU)"
        case "mesa:zink":  return "Mesa Zink (Vulkan)"
        case "mesa:swr":   return "Mesa SWR (CPU/AVX)"
        case "gptk":       return "GPTK (D3DMetal, copy DLLs)"
        case "gptk_full":  return "GPTK Full (Apple Toolkit)"
        default:           return b.label
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
