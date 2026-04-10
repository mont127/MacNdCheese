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
            VStack(alignment: .leading, spacing: 10) {
                Text(game.name)
                    .font(.title2)
                    .fontWeight(.bold)
                    .lineLimit(2)

                Text("App ID: \(game.appid)")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Spacer().frame(height: 0)

                // EXE picker
                VStack(alignment: .leading, spacing: 4) {
                    Text("EXE:")
                        .font(.caption)
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

                        HStack {
                            Button("Browse...") { browseExe() }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                        }
                    }
                }

                // Graphics engine picker
                VStack(alignment: .leading, spacing: 4) {
                    Text("Graphics Engine:")
                        .font(.caption)
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
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fontWeight(.semibold)
                    TextField("Optional launch arguments...", text: $extraArgs)
                        .textFieldStyle(.roundedBorder)
                }

                // Retina mode
                VStack(alignment: .leading, spacing: 4) {
                    Toggle(isOn: $retinaMode) {
                        Text("Retina hi-res mode")
                            .font(.caption)
                            .fontWeight(.semibold)
                    }
                    Text("Enable high resolution for retina screens. Game performance might be affected.")
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
                        .font(.caption)
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

                    Text("These options control Wine synchronization. MSync is macOS-specific and usually should not be combined with ESync.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                Spacer()

                // Buttons
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
                            Text("PLAY")
                                .fontWeight(.bold)
                        }
                        .frame(minWidth: 100)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.cyan)
                    .controlSize(.large)
                    .keyboardShortcut(.defaultAction)
                    .disabled(effectiveExe.isEmpty || isLaunching)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(24)
        .frame(width: 560, height: 460)
        .background(.ultraThinMaterial)
        .task {
            await loadExes()
            await loadBackends()
            await loadBottleDefaults()
        }
    }

    private func loadExes() async {
        loadingExes = true
        detectedExes = await backend.detectExes(installDir: game.installDir)
        // Pre-select the game's detected exe
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
        let exe = effectiveExe
        guard !exe.isEmpty else { return }
        isLaunching = true
        Task {
            let sync = normalizedSyncSelection()
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
                steamAppId: game.isManual ? "" : game.appid
            )
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
        // Show relative to install dir if possible
        let installDir = game.installDir
        if !installDir.isEmpty, path.hasPrefix(installDir) {
            let relative = String(path.dropFirst(installDir.count))
            return relative.hasPrefix("/") ? String(relative.dropFirst()) : relative
        }
        return URL(fileURLWithPath: path).lastPathComponent
    }
}
