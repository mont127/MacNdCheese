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

    private var effectiveExe: String {
        if !selectedExe.isEmpty { return selectedExe }
        return game.exe ?? ""
    }

    var body: some View {
        HStack(alignment: .top, spacing: 24) {
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
            VStack(alignment: .leading, spacing: 14) {
                Text(game.name)
                    .font(.title2)
                    .fontWeight(.bold)
                    .lineLimit(2)

                Text("App ID: \(game.appid)")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                Spacer().frame(height: 2)

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

                // Backend picker
                VStack(alignment: .leading, spacing: 4) {
                    Text("Graphics Backend:")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fontWeight(.semibold)

                    if loadingBackends {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("Detecting...").font(.caption).foregroundStyle(.secondary)
                        }
                    } else {
                        Picker("", selection: $selectedBackend) {
                            ForEach(availableBackends) { b in
                                Text(b.label)
                                    .tag(b.backendId)
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
                    Text("Enable high resolution for retina screens. Game compatibility might be affected.")
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
        .frame(width: 560, height: 380)
        .background(.ultraThinMaterial)
        .task {
            await loadExes()
            await loadBackends()
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

    private func launchGame() {
        guard let prefix = backend.activePrefix else { return }
        let exe = effectiveExe
        guard !exe.isEmpty else { return }
        isLaunching = true
        Task {
            await backend.launchGame(prefix: prefix, exe: exe, args: extraArgs, backend: selectedBackend, installDir: game.installDir, retinaMode: retinaMode)
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
