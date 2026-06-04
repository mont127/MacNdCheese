import SwiftUI
import UniformTypeIdentifiers

private enum InstallerPathStore {
    static let dxvkSrcKey = "installerPaths.dxvkSrc"
    static let dxvkInstallKey = "installerPaths.dxvkInstall"
    static let dxvkInstall32Key = "installerPaths.dxvkInstall32"
    static let steamSetupKey = "installerPaths.steamSetup"
    static let mesaDirKey = "installerPaths.mesaDir"
    static let dxmtDirKey = "installerPaths.dxmtDir"
    static let vkd3dDirKey = "installerPaths.vkd3dDir"
    static let gptkDirKey = "installerPaths.gptkDir"

    static var defaultDXVKSrc: String { NSHomeDirectory() + "/DXVK-macOS" }
    static var defaultDXVKInstall: String { NSHomeDirectory() + "/dxvk-release" }
    static var defaultDXVKInstall32: String { NSHomeDirectory() + "/dxvk-release-32" }
    static var defaultSteamSetup: String { NSHomeDirectory() + "/Downloads/SteamSetup.exe" }
    static var defaultMesaDir: String { NSHomeDirectory() + "/mesa/x64" }
    static var defaultDXMTDir: String { NSHomeDirectory() + "/dxmt" }
    static var defaultVKD3DDir: String { NSHomeDirectory() + "/vkd3d-proton" }
    static var defaultGPTKDir: String {
        let home = NSHomeDirectory()
        let bundledCandidate = home + "/macndcheese/gptk"
        return FileManager.default.fileExists(atPath: bundledCandidate) ? bundledCandidate : home + "/gptk"
    }

    static func current() -> InstallerPaths {
        InstallerPaths(
            dxvkSrc: value(for: dxvkSrcKey, default: defaultDXVKSrc),
            dxvkInstall64: value(for: dxvkInstallKey, default: defaultDXVKInstall),
            dxvkInstall32: value(for: dxvkInstall32Key, default: defaultDXVKInstall32),
            steamSetup: value(for: steamSetupKey, default: defaultSteamSetup),
            mesaDir: value(for: mesaDirKey, default: defaultMesaDir),
            dxmtDir: value(for: dxmtDirKey, default: defaultDXMTDir),
            vkd3dDir: value(for: vkd3dDirKey, default: defaultVKD3DDir),
            gptkDir: value(for: gptkDirKey, default: defaultGPTKDir)
        )
    }

    private static func value(for key: String, default defaultValue: String) -> String {
        guard let stored = UserDefaults.standard.string(forKey: key), !stored.isEmpty else {
            return defaultValue
        }
        return stored
    }
}

private struct InstallerPaths {
    let dxvkSrc: String
    let dxvkInstall64: String
    let dxvkInstall32: String
    let steamSetup: String
    let mesaDir: String
    let dxmtDir: String
    let vkd3dDir: String
    let gptkDir: String
}

struct SettingsSheet: View {
    @EnvironmentObject var backend: BackendClient
    @EnvironmentObject var loc: LocalizationManager
    @Environment(\.dismiss) private var dismiss
    @State private var selectedTab = "bottle"

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text(L("Settings"))
                    .font(.title2)
                    .fontWeight(.bold)
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title2)
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }
            .padding(20)

            // Tab picker
            Picker("", selection: $selectedTab) {
                Text(L("Bottle")).tag("bottle")
                Text(L("Paths")).tag("paths")
                Text(L("Setup")).tag("setup")
                Text(L("Diagnose")).tag("diagnose")
                Text(L("Language")).tag("language")
                Text(L("Logs")).tag("logs")
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 20)

            Divider().padding(.top, 12)

            // Tab content
            Group {
                switch selectedTab {
                case "bottle": BottleSettingsTab()
                case "paths": PathsSettingsTab()
                case "setup": SetupSettingsTab()
                case "diagnose": DiagnoseSettingsTab()
                case "language": LanguageSettingsTab()
                case "logs": LogsSettingsTab()
                default: EmptyView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(width: 680, height: 620)
        .background(.ultraThinMaterial)
        .onAppear {
            // First-run onboarding: the language popup sets this flag so the
            // user lands directly on the Setup tab to install everything.
            if UserDefaults.standard.bool(forKey: LanguagePickerSheet.showSetupFlag) {
                UserDefaults.standard.removeObject(forKey: LanguagePickerSheet.showSetupFlag)
                selectedTab = "setup"
            }
        }
    }
}




// MARK: - Bottle Tab

struct BottleSettingsTab: View {
    @EnvironmentObject var backend: BackendClient
    @State private var bottleName = ""
    @State private var launcherExe = ""
    @State private var iconPath = ""
    @State private var wineBinary = "auto"
    @State private var metalHud = false
    @State private var isInitializing = false
    @State private var isCleaning = false
    @State private var isOpeningWinecfg = false
    @State private var isMoving = false

    private var activeBottle: Bottle? {
        guard let prefix = backend.activePrefix else { return nil }
        return backend.bottles.first { $0.path == prefix }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let bottle = activeBottle {
                    // Prefix path (read-only)
                    SettingsRow(label: L("Prefix path")) {
                        Text(bottle.path.replacingOccurrences(of: NSHomeDirectory(), with: "~"))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .textSelection(.enabled)
                    }

                    // Bottle name
                    SettingsRow(label: L("Bottle Name")) {
                        TextField(L("Display name"), text: $bottleName)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit { saveBottleConfig() }
                    }

                    // Launcher exe
                    SettingsRow(label: L("Launcher exe")) {
                        HStack {
                            TextField(L("Leave empty for Steam (default)"), text: $launcherExe)
                                .textFieldStyle(.roundedBorder)
                                .onSubmit { saveBottleConfig() }
                            Button(L("Browse")) { browseLauncherExe() }
                        }
                    }

                    // Custom icon
                    SettingsRow(label: L("Custom icon (PNG)")) {
                        HStack {
                            TextField(L("Leave empty for default"), text: $iconPath)
                                .textFieldStyle(.roundedBorder)
                                .onSubmit { saveBottleConfig() }
                            Button(L("Browse")) { browseIcon() }
                        }
                    }

                    // Wine version
                    SettingsRow(label: L("Wine")) {
                        Picker("", selection: $wineBinary) {
                            Text(L("Auto (prefer Stable)")).tag("auto")
                            Text(L("Stable")).tag("stable")
                            Text(L("Staging")).tag("staging")
                        }
                        .labelsHidden()
                    }

                    // Metal HUD (global for this prefix)
                    Toggle(isOn: $metalHud) {
                        Text(L("Metal HUD"))
                            .font(.body)
                    }
                    .onChange(of: metalHud) { saveBottleConfig() }

                    Divider()

                    // Action buttons
                    Text(L("Prefix Tools"))
                        .font(.headline)
                        .padding(.top, 4)

                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                        ActionButton(
                            title: L("Initialize Prefix"),
                            subtitle: L("Run wineboot to create drive_c"),
                            icon: "plus.circle",
                            isLoading: isInitializing
                        ) {
                            isInitializing = true
                            Task {
                                await backend.initPrefix(prefix: bottle.path)
                                isInitializing = false
                            }
                        }

                        ActionButton(
                            title: L("Clean Prefix"),
                            subtitle: L("Run wineboot -u to update"),
                            icon: "arrow.triangle.2.circlepath",
                            isLoading: isCleaning
                        ) {
                            isCleaning = true
                            Task {
                                await backend.cleanPrefix(prefix: bottle.path)
                                isCleaning = false
                            }
                        }

                        ActionButton(
                            title: L("Winecfg"),
                            subtitle: L("Open Wine configuration"),
                            icon: "slider.horizontal.3",
                            isLoading: isOpeningWinecfg
                        ) {
                            isOpeningWinecfg = true
                            Task {
                                await backend.openWinecfg(prefix: bottle.path)
                                isOpeningWinecfg = false
                            }
                        }

                        ActionButton(
                            title: L("Open SteamSetup"),
                            subtitle: L("Install or repair Steam"),
                            icon: "arrow.down.circle"
                        ) {
                            openSteamSetup(prefix: bottle.path)
                        }

                        ActionButton(
                            title: L("Open in Finder"),
                            subtitle: L("Show prefix folder"),
                            icon: "folder"
                        ) {
                            Task { await backend.openPrefixFolder(prefix: bottle.path) }
                        }

                        ActionButton(
                            title: L("Move Prefix"),
                            subtitle: L("Move this bottle folder"),
                            icon: "folder.badge.gearshape",
                            isLoading: isMoving
                        ) {
                            movePrefix(path: bottle.path)
                        }

                        ActionButton(
                            title: L("Kill Wineserver"),
                            subtitle: L("Force stop all Wine processes"),
                            icon: "xmark.octagon",
                            tint: .red
                        ) {
                            Task { await backend.killWineserver(prefix: bottle.path) }
                        }

                        ActionButton(
                            title: L("Delete Prefix"),
                            subtitle: L("Permanently remove from disk"),
                            icon: "trash",
                            tint: .red
                        ) {
                            Task { await backend.deleteBottle(path: bottle.path) }
                        }
                    }

                    // Save button
                    HStack {
                        Spacer()
                        Button(L("Save Changes")) { saveBottleConfig() }
                            .buttonStyle(.borderedProminent)
                            .tint(.cyan)
                    }
                    .padding(.top, 8)

                } else {
                    Text(L("Select a bottle in the sidebar to configure it."))
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, 40)
                }
            }
            .padding(20)
        }
        .onAppear { loadFields() }
        .onChange(of: backend.activePrefix) { loadFields() }
    }

    private func loadFields() {
        if let bottle = activeBottle {
            bottleName = bottle.name
            launcherExe = bottle.launcherExe ?? ""
            iconPath = bottle.iconPath ?? ""
            wineBinary = bottle.wineBinary ?? "auto"
            Task {
                if let config = await backend.getBottleConfig(path: bottle.path) {
                    metalHud = config["metal_hud"] as? Bool ?? false
                }
            }
        }
    }

    private func saveBottleConfig() {
        guard let prefix = backend.activePrefix else { return }
        Task {
            await backend.setBottleConfig(path: prefix, values: [
                "name": bottleName,
                "launcher_exe": launcherExe,
                "icon_path": iconPath,
                "wine_binary": wineBinary,
                "metal_hud": metalHud,
            ])
        }
    }

    private func browseLauncherExe() {
        let panel = NSOpenPanel()
        // Allow .exe and .msi (Windows Installer packages run via msiexec).
        var types: [UTType] = [.exe]
        if let msi = UTType(filenameExtension: "msi") { types.append(msi) }
        panel.allowedContentTypes = types
        panel.canChooseFiles = true
        if panel.runModal() == .OK, let url = panel.url {
            launcherExe = url.path
        }
    }

    private func browseIcon() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png, .jpeg]
        panel.canChooseFiles = true
        if panel.runModal() == .OK, let url = panel.url {
            iconPath = url.path
        }
    }

    private func openSteamSetup(prefix: String) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.exe]
        panel.canChooseFiles = true
        panel.title = L("Select SteamSetup.exe")
        panel.nameFieldStringValue = "SteamSetup.exe"
        if panel.runModal() == .OK, let url = panel.url {
            Task {
                await backend.runExe(prefix: prefix, exe: url.path)
            }
        }
    }

    private func movePrefix(path: String) {
        let panel = NSSavePanel()
        panel.canCreateDirectories = true
        panel.title = L("Move Prefix")
        panel.prompt = L("Move")
        panel.nameFieldStringValue = URL(fileURLWithPath: path).lastPathComponent
        panel.directoryURL = URL(fileURLWithPath: path).deletingLastPathComponent()
        if panel.runModal() == .OK, let url = panel.url {
            isMoving = true
            Task {
                _ = await backend.moveBottle(path: path, destinationPath: url.path)
                isMoving = false
            }
        }
    }
}

// MARK: - Paths Tab

struct PathsSettingsTab: View {
    @EnvironmentObject var backend: BackendClient

    @AppStorage(InstallerPathStore.dxvkSrcKey) private var dxvkSrc = InstallerPathStore.defaultDXVKSrc
    @AppStorage(InstallerPathStore.dxvkInstallKey) private var dxvkInstall = InstallerPathStore.defaultDXVKInstall
    @AppStorage(InstallerPathStore.dxvkInstall32Key) private var dxvkInstall32 = InstallerPathStore.defaultDXVKInstall32
    @AppStorage(InstallerPathStore.steamSetupKey) private var steamSetup = InstallerPathStore.defaultSteamSetup
    @AppStorage(InstallerPathStore.mesaDirKey) private var mesaDir = InstallerPathStore.defaultMesaDir
    @AppStorage(InstallerPathStore.dxmtDirKey) private var dxmtDir = InstallerPathStore.defaultDXMTDir
    @AppStorage(InstallerPathStore.vkd3dDirKey) private var vkd3dDir = InstallerPathStore.defaultVKD3DDir
    @AppStorage(InstallerPathStore.gptkDirKey) private var gptkDir = InstallerPathStore.defaultGPTKDir

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                PathRow(label: L("DXVK source"), path: $dxvkSrc, isDir: true)
                PathRow(label: L("DXVK install (64-bit)"), path: $dxvkInstall, isDir: true)
                PathRow(label: L("DXVK install (32-bit)"), path: $dxvkInstall32, isDir: true)
                PathRow(label: L("SteamSetup.exe"), path: $steamSetup, isDir: false)
                PathRow(label: L("Mesa x64 dir"), path: $mesaDir, isDir: true)
                PathRow(label: L("DXMT dir"), path: $dxmtDir, isDir: true)
                PathRow(label: L("VKD3D-Proton dir"), path: $vkd3dDir, isDir: true)
                PathRow(label: L("GPTK dir"), path: $gptkDir, isDir: true)
            }
            .padding(20)
        }
    }
}

struct PathRow: View {
    let label: String
    @Binding var path: String
    let isDir: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                TextField(label, text: $path)
                    .textFieldStyle(.roundedBorder)
                Button(L("Browse")) {
                    if isDir {
                        let panel = NSOpenPanel()
                        panel.canChooseFiles = false
                        panel.canChooseDirectories = true
                        if panel.runModal() == .OK, let url = panel.url {
                            path = url.path
                        }
                    } else {
                        let panel = NSOpenPanel()
                        panel.canChooseFiles = true
                        panel.canChooseDirectories = false
                        if panel.runModal() == .OK, let url = panel.url {
                            path = url.path
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Setup Tab (Components)

struct SetupSettingsTab: View {
    @EnvironmentObject var backend: BackendClient
    @State private var isRunning = false
    @State private var isLoadingStatus = false

    // Toggle selections — each maps to one installer action
    @State private var wantTools = false
    @State private var wantWineStable = false
    @State private var wantWineStaging = false
    @State private var wantWineD3DMetal = false
    @State private var wantWineDevel = false
    @State private var wantDxvk = false
    @State private var wantVkd3d = false
    @State private var wantGptkDlls = false
    @State private var wantDxmt = false
    @State private var wantMesa = false
    @State private var wantWineOpenXR = false
    @State private var wantDxmtOpenXR = false
    @State private var wantMonadoRuntime = false

    // Baseline installed state (used to detect installs vs uninstalls)
    @State private var hadTools = false
    @State private var hadWineStable = false
    @State private var hadWineStaging = false
    @State private var hadWineD3DMetal = false
    @State private var hadWineDevel = false
    @State private var hadDxvk = false
    @State private var hadVkd3d = false
    @State private var hadGptkDlls = false
    @State private var hadDxmt = false
    @State private var hadMesa = false
    @State private var hadWineOpenXR = false
    @State private var hadDxmtOpenXR = false
    @State private var hadMonadoRuntime = false

    // Update availability per component
    @State private var toolsHasUpdate = false
    @State private var wineStableHasUpdate = false
    @State private var wineStagingHasUpdate = false
    @State private var stagingLatestName: String? = nil
    @State private var dxmtHasUpdate = false
    @State private var dxmtLatestName: String? = nil

    // Install progress
    @State private var installJobId: String? = nil
    @State private var installLogLines: [String] = []
    @State private var installLogOffset: Int = 0
    @State private var installCurrentAction: String = ""
    @State private var installDone: Bool = false
    @State private var installFailed: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                GroupBox(L("Quick Setup")) {
                    HStack(spacing: 12) {
                        Button(L("Minimal")) {
                            wantTools = true; wantWineStable = true
                            wantDxvk = true; wantMesa = true
                        }
                        .buttonStyle(.bordered)
                        .help(L("Select: Tools, Wine Stable, DXVK, Mesa"))
                        .disabled(isRunning)
                        Button(L("Everything")) {
                            wantTools = true; wantWineStable = true; wantWineStaging = true
                            wantDxvk = true; wantVkd3d = true
                            wantGptkDlls = true; wantDxmt = true; wantMesa = true
                        }
                        .buttonStyle(.bordered)
                        .help(L("Select all components"))
                        .disabled(isRunning)
                        Button(L("None")) {
                            wantTools = false; wantWineStable = false; wantWineStaging = false
                            wantDxvk = false; wantVkd3d = false
                            wantGptkDlls = false; wantDxmt = false; wantMesa = false
                        }
                        .buttonStyle(.bordered)
                        .disabled(isRunning)
                    }
                    .padding(8)
                }

                GroupBox(L("Tools")) {
                    VStack(alignment: .leading, spacing: 8) {
                        ComponentToggleRow(L("Tools (git, 7z, wget)"), isOn: $wantTools,
                                          installed: hadTools, updateAvailable: toolsHasUpdate)
                            .disabled(isRunning)
                    }
                    .padding(8)
                }

                GroupBox(L("Wine (Translation Engine)")) {
                    VStack(alignment: .leading, spacing: 8) {
                        ComponentToggleRow(L("Wine (Stable)"), isOn: $wantWineStable,
                                          installed: hadWineStable, updateAvailable: wineStableHasUpdate)
                            .disabled(isRunning)
                        ComponentToggleRow(stagingLatestName.map { String(format: L("Wine (Staging — %@)"), $0) } ?? L("Wine (Staging)"),
                                          isOn: $wantWineStaging,
                                          installed: hadWineStaging, updateAvailable: wineStagingHasUpdate)
                            .disabled(isRunning)
                        ComponentToggleRow(L("Wine Devel (SDL3/OpenGL, ~310 MB)"),
                                          isOn: $wantWineDevel,
                                          installed: hadWineDevel)
                            .disabled(isRunning)
                            .help(L("Standalone Wine Staging 11.8 with the OpenGL 3.2+ macdrv patch, for SDL3/OpenGL games (e.g. Mewgenics). Downloaded on install. Independent build."))
                    }
                    .padding(8)
                }

                GroupBox(L("Graphics")) {
                    VStack(alignment: .leading, spacing: 8) {
                        ComponentToggleRow(dxmtLatestName.map { String(format: L("DXMT (%@)"), $0) } ?? L("DXMT"),
                                          isOn: $wantDxmt, installed: hadDxmt, updateAvailable: dxmtHasUpdate)
                            .disabled(isRunning)
                        ComponentToggleRow(L("DXVK"), isOn: $wantDxvk, installed: hadDxvk)
                            .disabled(isRunning)
                        ComponentToggleRow(L("VKD3D-Proton"), isOn: $wantVkd3d, installed: hadVkd3d)
                            .disabled(isRunning)
                        ComponentToggleRow(L("Wine D3DMetal (shimless, ~888 MB)"),
                                          isOn: $wantWineD3DMetal, installed: hadWineD3DMetal)
                            .disabled(isRunning)
                            .help(L("No-shim patched Wine 11.0 + Apple D3DMetal. Removes the gs.base swap so D3D11/12 games talk to Apple's D3DMetal framework with no DYLD shim — powers the D3DMetal launch engine. Bundled with the app; unzips on install."))
                        ComponentToggleRow(L("Mesa"), isOn: $wantMesa, installed: hadMesa)
                            .disabled(isRunning)
                    }
                    .padding(8)
                }

                GroupBox(L("VR")) {
                    VStack(alignment: .leading, spacing: 8) {
                        ComponentToggleRow(L("wineopenxr (D3D11 OpenXR bridge, builds from source)"),
                                          isOn: $wantWineOpenXR,
                                          installed: hadWineOpenXR)
                            .disabled(isRunning)
                            .help(L("Clones monofunc/wineopenxr, builds it (needs cmake + mingw-w64), and registers it as the active OpenXR runtime so D3D11 OpenXR apps can talk to a native macOS OpenXR runtime via DXMT."))
                        ComponentToggleRow(L("DXMT + OpenXR fork (monofunc/dxmt, builds from source)"),
                                          isOn: $wantDxmtOpenXR,
                                          installed: hadDxmtOpenXR)
                            .disabled(isRunning)
                            .help(L("Builds monofunc/dxmt (feature/openxr) — DXMT's Metal D3D11/10 translation plus OpenXR passthrough — with meson + mingw-w64 + llvm@15. Installs it as the \"DXMT + OpenXR (VR)\" graphics backend and pulls in wineopenxr so D3D11 VR apps reach the native macOS OpenXR runtime. Set DXMT_OPENXR_URL to install a prebuilt build instead."))
                        ComponentToggleRow(L("Monado OpenXR runtime (x86_64, builds from source)"),
                                          isOn: $wantMonadoRuntime,
                                          installed: hadMonadoRuntime)
                            .disabled(isRunning)
                            .help(L("Builds Monado as an x86_64 OpenXR runtime and registers it. The wineopenxr bridge forwards D3D11 OpenXR to this runtime, which is loaded into the x86_64 (Rosetta) Wine process — so it MUST be x86_64. Without this, an arm64 system Monado fails with 'incompatible architecture' and VR won't start. Builds with cmake + the x86_64 Homebrew Vulkan/MoltenVK deps (slow)."))
                    }
                    .padding(8)
                }

                // Progress / log area
                if isRunning || installDone {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            if isRunning {
                                ProgressView().controlSize(.small)
                            } else if installFailed {
                                Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
                            } else {
                                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                            }
                            Text(isRunning
                                 ? (installCurrentAction.isEmpty ? L("Starting...") : installCurrentAction)
                                 : (installFailed ? L("Finished with errors") : L("Done!")))
                                .font(.caption)
                                .foregroundColor(isRunning ? .secondary : (installFailed ? .red : .green))
                            Spacer()
                            if installDone {
                                Button(L("Dismiss")) { clearInstallState() }
                                    .buttonStyle(.bordered)
                                    .controlSize(.small)
                            }
                        }

                        ScrollViewReader { proxy in
                            ScrollView {
                                Text(installLogLines.joined(separator: "\n"))
                                    .font(.system(.caption2, design: .monospaced))
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .textSelection(.enabled)
                                    .id("logBottom")
                            }
                            .frame(height: 140)
                            .background(.black.opacity(0.25))
                            .clipShape(RoundedRectangle(cornerRadius: 6))
                            .onChange(of: installLogLines) {
                                proxy.scrollTo("logBottom", anchor: .bottom)
                            }
                        }
                    }
                }

                HStack {
                    if isLoadingStatus {
                        ProgressView().controlSize(.small)
                        Text(L("Checking components..."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(L("Update")) { runUpdate() }
                        .buttonStyle(.borderedProminent)
                        .tint(.cyan)
                        .disabled(isRunning || isLoadingStatus)
                }
            }
            .padding(20)
        }
        .onAppear { loadComponentStatus() }
    }

    private func clearInstallState() {
        installJobId = nil
        installLogLines = []
        installLogOffset = 0
        installCurrentAction = ""
        installDone = false
        installFailed = false
    }

    private func loadComponentStatus() {
        isLoadingStatus = true
        Task {
            if let status = await backend.getComponentsStatus() {
                hadTools = status.hasTools;             wantTools = status.hasTools
                hadWineStable = status.hasWineStable;   wantWineStable = status.hasWineStable
                hadWineStaging = status.hasWineStaging; wantWineStaging = status.hasWineStaging
                hadWineD3DMetal = status.hasWineD3DMetal; wantWineD3DMetal = status.hasWineD3DMetal
                hadWineDevel = status.hasWineDevel;     wantWineDevel = status.hasWineDevel
                hadDxvk = status.hasDxvk64;             wantDxvk = status.hasDxvk64
                hadVkd3d = status.hasVkd3d;             wantVkd3d = status.hasVkd3d
                hadGptkDlls = status.hasGptkDlls;       wantGptkDlls = status.hasGptkDlls
                hadDxmt = status.hasDxmt;               wantDxmt = status.hasDxmt
                hadWineOpenXR = status.hasWineOpenXR;   wantWineOpenXR = status.hasWineOpenXR
                hadDxmtOpenXR = status.hasDxmtOpenXR;   wantDxmtOpenXR = status.hasDxmtOpenXR
                hadMonadoRuntime = status.hasMonadoRuntime; wantMonadoRuntime = status.hasMonadoRuntime
                hadMesa = status.hasMesa;               wantMesa = status.hasMesa
            }
            isLoadingStatus = false

            if let info = await backend.getUpdateInfo() {
                toolsHasUpdate = info.toolsUpdateAvailable
                wineStableHasUpdate = info.wineStableUpdateAvailable
                wineStagingHasUpdate = info.wineStagingUpdateAvailable
                stagingLatestName = info.gcenxLatestName
                dxmtHasUpdate = info.dxmtUpdateAvailable
                dxmtLatestName = info.dxmtLatestName
            }
        }
    }

    private func runUpdate() {
        let home = NSHomeDirectory()
        let resourcePath = Bundle.main.resourcePath ?? Bundle.main.bundlePath
        let candidates = [resourcePath + "/installer.sh", home + "/macndcheese/installer.sh"]
        guard let installerPath = candidates.first(where: { FileManager.default.fileExists(atPath: $0) }) else {
            return
        }

        let prefix = backend.activePrefix ?? home + "/wined"
        let pathSettings = InstallerPathStore.current()
        let mesaUrl = "https://github.com/pal1000/mesa-dist-win/releases/download/23.1.9/mesa3d-23.1.9-release-msvc.7z"

        // Plan actions: install if toggled on, uninstall if toggled off but was installed
        var uninstallActions: [String] = []
        var installActions: [String] = []
        func plan(_ on: Bool, _ was: Bool, install: String, uninstall: String) {
            if on { installActions.append(install) }
            else if was { uninstallActions.append(uninstall) }
        }
        plan(wantTools,       hadTools,       install: "install_tools",        uninstall: "uninstall_tools")
        plan(wantWineStable,  hadWineStable,  install: "install_wine",         uninstall: "uninstall_wine")
        plan(wantWineStaging, hadWineStaging, install: "install_wine_staging", uninstall: "uninstall_wine_staging")
        plan(wantWineD3DMetal, hadWineD3DMetal,
             install: "install_wine_d3dmetal", uninstall: "uninstall_wine_d3dmetal")
        plan(wantWineDevel, hadWineDevel,
             install: "install_wine_devel", uninstall: "uninstall_wine_devel")
        plan(wantDxvk,        hadDxvk,        install: "install_dxvk",         uninstall: "uninstall_dxvk")
        plan(wantVkd3d,       hadVkd3d,       install: "install_vkd3d",        uninstall: "uninstall_vkd3d")
        plan(wantGptkDlls,    hadGptkDlls,    install: "install_gptk_dlls",    uninstall: "uninstall_gptk_dlls")
        plan(wantDxmt,        hadDxmt,        install: "install_dxmt",         uninstall: "uninstall_dxmt")
        plan(wantMesa,        hadMesa,        install: "install_mesa",         uninstall: "uninstall_mesa")
        plan(wantWineOpenXR,  hadWineOpenXR,  install: "install_wineopenxr",   uninstall: "uninstall_wineopenxr")
        plan(wantDxmtOpenXR,  hadDxmtOpenXR,  install: "install_dxmt_openxr",  uninstall: "uninstall_dxmt_openxr")
        plan(wantMonadoRuntime, hadMonadoRuntime, install: "install_monado_runtime", uninstall: "uninstall_monado_runtime")

        let allActions = uninstallActions + installActions
        guard !allActions.isEmpty else { return }

        clearInstallState()
        isRunning = true

        Task {
            guard let jobId = await backend.runInstaller(
                installerPath: installerPath,
                actions: allActions,
                prefix: prefix,
                dxvkSrc: pathSettings.dxvkSrc,
                dxvk64: pathSettings.dxvkInstall64,
                dxvk32: pathSettings.dxvkInstall32,
                mesa: pathSettings.mesaDir,
                mesaUrl: mesaUrl,
                dxmt: pathSettings.dxmtDir,
                vkd3d: pathSettings.vkd3dDir,
                gptkDir: pathSettings.gptkDir
            ) else {
                isRunning = false
                return
            }

            installJobId = jobId
            while true {
                try? await Task.sleep(nanoseconds: 500_000_000)
                guard let progress = await backend.getInstallProgress(jobId: jobId, offset: installLogOffset) else {
                    break
                }
                installLogLines.append(contentsOf: progress.lines)
                installLogOffset = progress.totalLines
                installCurrentAction = progress.current
                if progress.done {
                    installDone = true
                    installFailed = progress.failed
                    isRunning = false
                    await backend.loadStatus()
                    loadComponentStatus()
                    break
                }
            }
        }
    }
}

struct ComponentToggleRow: View {
    let label: String
    @Binding var isOn: Bool
    let installed: Bool
    var updateAvailable: Bool = false

    init(_ label: String, isOn: Binding<Bool>, installed: Bool, updateAvailable: Bool = false) {
        self.label = label
        _isOn = isOn
        self.installed = installed
        self.updateAvailable = updateAvailable
    }

    var body: some View {
        HStack {
            Toggle(label, isOn: $isOn)
            Spacer()
            if updateAvailable {
                Text(L("Update available"))
                    .font(.caption2)
                    .foregroundStyle(.yellow)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.yellow.opacity(0.15), in: Capsule())
            } else if installed {
                Text(L("Installed"))
                    .font(.caption2)
                    .foregroundStyle(.green)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.green.opacity(0.15), in: Capsule())
            }
        }
    }
}

// MARK: - Diagnose Tab

struct DiagnoseSettingsTab: View {
    @EnvironmentObject var backend: BackendClient
    @State private var diagnosis: CheeseDiagnosis?
    @State private var isDiagnosing = false
    @State private var pendingRepair: CheeseRepairAction?

    @State private var repairJobId: String?
    @State private var repairLogLines: [String] = []
    @State private var repairLogOffset = 0
    @State private var repairCurrentAction = ""
    @State private var repairDone = false
    @State private var repairFailed = false
    @State private var isRepairing = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L("Diagnose Cheese"))
                            .font(.headline)
                        Text(activePrefixLabel)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .textSelection(.enabled)
                    }

                    Spacer()

                    Button {
                        runDiagnosis()
                    } label: {
                        Label(isDiagnosing ? L("Scanning") : L("Run Diagnosis"), systemImage: "stethoscope")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.cyan)
                    .disabled(isDiagnosing || isRepairing)
                }

                if isDiagnosing {
                    HStack {
                        ProgressView().controlSize(.small)
                        Text(L("Scanning MacNCheese, Wine and the selected prefix..."))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if let diagnosis {
                    DiagnosisSummaryView(diagnosis: diagnosis)

                    if !diagnosis.repairs.isEmpty {
                        GroupBox(L("Suggested Repairs")) {
                            VStack(spacing: 10) {
                                ForEach(diagnosis.repairs) { repair in
                                    RepairActionRow(repair: repair, disabled: isDiagnosing || isRepairing) {
                                        pendingRepair = repair
                                    }
                                }
                            }
                            .padding(8)
                        }
                    }

                    GroupBox(L("Checks")) {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(diagnosis.checks) { check in
                                DiagnosticCheckRow(check: check)
                            }
                        }
                        .padding(8)
                    }
                } else if !isDiagnosing {
                    VStack(alignment: .center, spacing: 8) {
                        Image(systemName: "stethoscope")
                            .font(.largeTitle)
                            .foregroundStyle(.secondary)
                        Text(L("Run a diagnosis to scan for missing components, corrupted Wine files and prefix loader failures."))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 44)
                }

                if isRepairing || repairDone {
                    repairProgressView
                }

                if let error = backend.lastError {
                    HStack {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.red)
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .lineLimit(2)
                    }
                }
            }
            .padding(20)
        }
        .onAppear {
            if diagnosis == nil {
                runDiagnosis()
            }
        }
        .confirmationDialog(
            L("Run Repair?"),
            isPresented: Binding(
                get: { pendingRepair != nil },
                set: { if !$0 { pendingRepair = nil } }
            ),
            presenting: pendingRepair
        ) { repair in
            Button(repair.title, role: repair.destructive ? .destructive : nil) {
                runRepair(repair)
            }
            Button(L("Cancel"), role: .cancel) {
                pendingRepair = nil
            }
        } message: { repair in
            Text(repair.details)
        }
    }

    private var activePrefixLabel: String {
        let prefix = backend.activePrefix ?? NSHomeDirectory() + "/wined"
        return prefix.replacingOccurrences(of: NSHomeDirectory(), with: "~")
    }

    private var repairStatusColor: Color {
        if isRepairing { return .secondary }
        return repairFailed ? .red : .green
    }

    private var repairProgressView: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                if isRepairing {
                    ProgressView().controlSize(.small)
                } else if repairFailed {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
                } else {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                }

                Text(isRepairing
                     ? (repairCurrentAction.isEmpty ? L("Repair running...") : repairCurrentAction)
                     : (repairFailed ? L("Repair finished with errors") : L("Repair complete")))
                    .font(.caption)
                    .foregroundColor(repairStatusColor)

                Spacer()

                if repairDone {
                    Button(L("Dismiss")) { clearRepairState() }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
            }

            ScrollViewReader { proxy in
                ScrollView {
                    Text(repairLogLines.joined(separator: "\n"))
                        .font(.system(.caption2, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .id("repairLogBottom")
                }
                .frame(height: 130)
                .background(.black.opacity(0.25))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .onChange(of: repairLogLines) {
                    proxy.scrollTo("repairLogBottom", anchor: .bottom)
                }
            }
        }
    }

    private func runDiagnosis() {
        guard !isDiagnosing else { return }
        isDiagnosing = true
        Task {
            diagnosis = await backend.diagnoseCheese(prefix: backend.activePrefix)
            isDiagnosing = false
        }
    }

    private func clearRepairState() {
        repairJobId = nil
        repairLogLines = []
        repairLogOffset = 0
        repairCurrentAction = ""
        repairDone = false
        repairFailed = false
        isRepairing = false
    }

    private func runRepair(_ repair: CheeseRepairAction) {
        pendingRepair = nil
        clearRepairState()
        isRepairing = true

        Task {
            guard let jobId = await backend.runCheeseRepair(action: repair.id, prefix: backend.activePrefix) else {
                isRepairing = false
                return
            }
            repairJobId = jobId

            while true {
                try? await Task.sleep(nanoseconds: 500_000_000)
                guard let progress = await backend.getInstallProgress(jobId: jobId, offset: repairLogOffset) else {
                    break
                }

                repairLogLines.append(contentsOf: progress.lines)
                repairLogOffset = progress.totalLines
                repairCurrentAction = progress.current

                if progress.done {
                    repairDone = true
                    repairFailed = progress.failed
                    isRepairing = false
                    await backend.loadStatus()
                    diagnosis = await backend.diagnoseCheese(prefix: backend.activePrefix)
                    break
                }
            }
        }
    }
}

struct DiagnosisSummaryView: View {
    let diagnosis: CheeseDiagnosis

    private var errorCount: Int {
        diagnosis.checks.filter { $0.status == "error" }.count
    }

    private var warningCount: Int {
        diagnosis.checks.filter { $0.status == "warning" }.count
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: errorCount > 0 ? "xmark.octagon.fill" : (warningCount > 0 ? "exclamationmark.triangle.fill" : "checkmark.circle.fill"))
                .foregroundStyle(errorCount > 0 ? .red : (warningCount > 0 ? .yellow : .green))
                .font(.title3)

            VStack(alignment: .leading, spacing: 2) {
                Text(diagnosis.summary)
                    .fontWeight(.semibold)
                Text(String(format: L("Generated %@"), diagnosis.generatedAt))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            Spacer()
        }
        .padding(10)
        .background(.black.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct DiagnosticCheckRow: View {
    let check: CheeseDiagnosticCheck

    private var color: Color {
        switch check.status {
        case "ok": return .green
        case "warning": return .yellow
        case "error": return .red
        default: return .blue
        }
    }

    private var icon: String {
        switch check.status {
        case "ok": return "checkmark.circle.fill"
        case "warning": return "exclamationmark.triangle.fill"
        case "error": return "xmark.octagon.fill"
        default: return "info.circle.fill"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Image(systemName: icon)
                    .foregroundStyle(color)
                    .frame(width: 18)
                Text(check.title)
                    .fontWeight(.medium)
                Spacer()
                Text(check.status.uppercased())
                    .font(.caption2)
                    .foregroundStyle(color)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(color.opacity(0.14), in: Capsule())
            }

            Text(check.message)
                .font(.caption)
                .foregroundStyle(.secondary)

            if !check.details.isEmpty {
                Text(check.details)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .lineLimit(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                    .background(.black.opacity(0.18), in: RoundedRectangle(cornerRadius: 6))
            }
        }
    }
}

struct RepairActionRow: View {
    let repair: CheeseRepairAction
    let disabled: Bool
    let action: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: repair.destructive ? "exclamationmark.arrow.triangle.2.circlepath" : "wrench.and.screwdriver")
                .foregroundStyle(repair.destructive ? .orange : .cyan)
                .frame(width: 22)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(repair.title)
                        .fontWeight(.medium)
                    if repair.recommended {
                        Text(L("Recommended"))
                            .font(.caption2)
                            .foregroundStyle(.cyan)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(.cyan.opacity(0.14), in: Capsule())
                    }
                }
                Text(repair.details)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Spacer()

            Button(L("Run")) { action() }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(disabled)
        }
    }
}

// MARK: - Logs Tab

struct LogsSettingsTab: View {
    @EnvironmentObject var backend: BackendClient
    @State private var logFiles: [(name: String, path: String)] = []
    @State private var selectedLog: String?
    @State private var logText = ""
    @State private var autoRefresh = true
    private let refreshTimer = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(L("Wine Logs"))
                    .font(.headline)

                Spacer()

                Button(L("Refresh")) { scanLogs(); loadSelectedLog() }
                    .buttonStyle(.bordered)
                    .controlSize(.small)

                Button(L("Open Log Folder")) {
                    NSWorkspace.shared.open(URL(fileURLWithPath: logDir))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }

            // Log file picker
            if !logFiles.isEmpty {
                Picker(L("Log file:"), selection: Binding(
                    get: { selectedLog ?? "" },
                    set: { selectedLog = $0; loadSelectedLog() }
                )) {
                    ForEach(logFiles, id: \.path) { file in
                        Text(file.name).tag(file.path)
                    }
                }
                .labelsHidden()
            }

            // Log content
            ScrollViewReader { proxy in
                ScrollView {
                    Text(logText.isEmpty ? L("No log content. Launch a game first.") : logText)
                        .font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .id("logBottom")
                }
                .background(.black.opacity(0.2))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .onChange(of: logText) {
                    proxy.scrollTo("logBottom", anchor: .bottom)
                }
            }

            HStack {
                Toggle(L("Auto-refresh"), isOn: $autoRefresh)
                    .toggleStyle(.checkbox)
                    .font(.caption)

                Spacer()

                if let error = backend.lastError {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.red)
                        .lineLimit(1)
                }
            }
        }
        .padding(20)
        .onAppear { scanLogs(); loadSelectedLog() }
        .onReceive(refreshTimer) { _ in
            if autoRefresh { loadSelectedLog() }
        }
    }

    private var logDir: String {
        NSHomeDirectory() + "/Library/Logs/MacNCheese"
    }

    private func scanLogs() {
        let fm = FileManager.default
        var result: [(name: String, path: String)] = []

        func addFiles(in dir: String, prefix: String, filter: (String) -> Bool) {
            guard let files = try? fm.contentsOfDirectory(atPath: dir) else { return }
            let sorted = files.filter(filter).sorted { lhs, rhs in
                let lDate = (try? fm.attributesOfItem(atPath: dir + "/" + lhs)[.modificationDate] as? Date) ?? .distantPast
                let rDate = (try? fm.attributesOfItem(atPath: dir + "/" + rhs)[.modificationDate] as? Date) ?? .distantPast
                return lDate > rDate
            }
            result.append(contentsOf: sorted.map { (name: prefix + $0, path: dir + "/" + $0) })
        }

        // App log first
        let appLog = logDir + "/macncheese.log"
        if fm.fileExists(atPath: appLog) {
            result.append((name: "macncheese.log (app)", path: appLog))
        }

        // Wine logs
        addFiles(in: logDir, prefix: "") { $0.hasSuffix("-wine.log") }

        // DXVK sublogs
        addFiles(in: logDir + "/dxvk", prefix: "dxvk/") { $0.hasSuffix(".log") }

        logFiles = result

        if selectedLog == nil || !logFiles.contains(where: { $0.path == selectedLog }) {
            selectedLog = logFiles.first?.path
        }
    }

    private func loadSelectedLog() {
        guard let path = selectedLog else {
            logText = ""
            return
        }
        do {
            let content = try String(contentsOfFile: path, encoding: .utf8)
            // Show last 500 lines to keep it responsive
            let lines = content.components(separatedBy: "\n")
            if lines.count > 500 {
                logText = String(format: L("... (%@ lines truncated) ..."), String(lines.count - 500)) + "\n" +
                    lines.suffix(500).joined(separator: "\n")
            } else {
                logText = content
            }
        } catch {
            logText = String(format: L("Failed to read log: %@"), error.localizedDescription)
        }
    }
}

// MARK: - Shared Components

struct SettingsRow<Content: View>: View {
    let label: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fontWeight(.semibold)
            content
        }
    }
}

struct ActionButton: View {
    let title: String
    var subtitle: String = ""
    let icon: String
    var tint: Color = .primary
    var isLoading: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                if isLoading {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: icon)
                        .frame(width: 20)
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(title)
                        .fontWeight(.medium)
                        .lineLimit(1)
                    if !subtitle.isEmpty {
                        Text(subtitle)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
                Spacer()
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .buttonStyle(.bordered)
        .tint(tint)
        .disabled(isLoading)
    }
}
