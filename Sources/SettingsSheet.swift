import SwiftUI

struct SettingsSheet: View {
    @EnvironmentObject var backend: BackendClient
    @Environment(\.dismiss) private var dismiss
    @State private var selectedTab = "bottle"

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("Settings")
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
                Text("Bottle").tag("bottle")
                Text("Paths").tag("paths")
                Text("Setup").tag("setup")
                Text("Logs").tag("logs")
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
                case "logs": LogsSettingsTab()
                default: EmptyView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(width: 620, height: 540)
        .background(.ultraThinMaterial)
    }
}

// MARK: - Bottle Tab

struct BottleSettingsTab: View {
    @EnvironmentObject var backend: BackendClient
    @State private var bottleName = ""
    @State private var launcherExe = ""
    @State private var iconPath = ""
    @State private var wineBinary = "auto"
<<<<<<< discord-rpc
    @State private var discordRpc = true
=======
    @State private var metalHud = false
>>>>>>> main
    @State private var isInitializing = false
    @State private var isCleaning = false

    private var activeBottle: Bottle? {
        guard let prefix = backend.activePrefix else { return nil }
        return backend.bottles.first { $0.path == prefix }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let bottle = activeBottle {
                    // Prefix path (read-only)
                    SettingsRow(label: "Prefix path") {
                        Text(bottle.path.replacingOccurrences(of: NSHomeDirectory(), with: "~"))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .textSelection(.enabled)
                    }

                    // Bottle name
                    SettingsRow(label: "Bottle Name") {
                        TextField("Display name", text: $bottleName)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit { saveBottleConfig() }
                    }

                    // Launcher exe
                    SettingsRow(label: "Launcher exe") {
                        HStack {
                            TextField("Leave empty for Steam (default)", text: $launcherExe)
                                .textFieldStyle(.roundedBorder)
                                .onSubmit { saveBottleConfig() }
                            Button("Browse") { browseLauncherExe() }
                        }
                    }

                    // Custom icon
                    SettingsRow(label: "Custom icon (PNG)") {
                        HStack {
                            TextField("Leave empty for default", text: $iconPath)
                                .textFieldStyle(.roundedBorder)
                                .onSubmit { saveBottleConfig() }
                            Button("Browse") { browseIcon() }
                        }
                    }

                    // Wine version
                    SettingsRow(label: "Wine") {
                        Picker("", selection: $wineBinary) {
                            Text("Auto (prefer Stable)").tag("auto")
                            Text("Stable").tag("stable")
                            Text("Staging").tag("staging")
                        }
                        .labelsHidden()
                    }

<<<<<<< discord-rpc
                    // Discord RPC — only show if rpc-bridge is installed
                    if backend.componentsStatus?.hasRpcBridge == true {
                        SettingsRow(label: "Discord Rich Presence") {
                            Toggle("", isOn: $discordRpc)
                                .labelsHidden()
                                .onChange(of: discordRpc) { saveBottleConfig() }
                        }
                    }
=======
                    // Metal HUD (global for this prefix)
                    Toggle(isOn: $metalHud) {
                        Text("Metal HUD")
                            .font(.body)
                    }
                    .onChange(of: metalHud) { saveBottleConfig() }
>>>>>>> main

                    Divider()

                    // Action buttons
                    Text("Prefix Tools")
                        .font(.headline)
                        .padding(.top, 4)

                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                        ActionButton(
                            title: "Initialize Prefix",
                            subtitle: "Run wineboot to create drive_c",
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
                            title: "Clean Prefix",
                            subtitle: "Run wineboot -u to update",
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
                            title: "Open SteamSetup",
                            subtitle: "Install or repair Steam",
                            icon: "arrow.down.circle"
                        ) {
                            openSteamSetup(prefix: bottle.path)
                        }

                        ActionButton(
                            title: "Open in Finder",
                            subtitle: "Show prefix folder",
                            icon: "folder"
                        ) {
                            Task { await backend.openPrefixFolder(prefix: bottle.path) }
                        }

                        ActionButton(
                            title: "Kill Wineserver",
                            subtitle: "Force stop all Wine processes",
                            icon: "xmark.octagon",
                            tint: .red
                        ) {
                            Task { await backend.killWineserver(prefix: bottle.path) }
                        }

                        ActionButton(
                            title: "Delete Prefix",
                            subtitle: "Permanently remove from disk",
                            icon: "trash",
                            tint: .red
                        ) {
                            Task { await backend.deleteBottle(path: bottle.path) }
                        }
                    }

                    // Save button
                    HStack {
                        Spacer()
                        Button("Save Changes") { saveBottleConfig() }
                            .buttonStyle(.borderedProminent)
                            .tint(.cyan)
                    }
                    .padding(.top, 8)

                } else {
                    Text("Select a bottle in the sidebar to configure it.")
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
<<<<<<< discord-rpc
            discordRpc = bottle.discordRpc ?? true
=======
            Task {
                if let config = await backend.getBottleConfig(path: bottle.path) {
                    metalHud = config["metal_hud"] as? Bool ?? false
                }
            }
>>>>>>> main
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
<<<<<<< discord-rpc
                "discord_rpc": discordRpc,
=======
                "metal_hud": metalHud,
>>>>>>> main
            ])
        }
    }

    private func browseLauncherExe() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.exe]
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
        panel.title = "Select SteamSetup.exe"
        panel.nameFieldStringValue = "SteamSetup.exe"
        if panel.runModal() == .OK, let url = panel.url {
            Task {
                await backend.runExe(prefix: prefix, exe: url.path)
            }
        }
    }
}

// MARK: - Paths Tab

struct PathsSettingsTab: View {
    @EnvironmentObject var backend: BackendClient

    @State private var dxvkSrc = NSHomeDirectory() + "/DXVK-macOS"
    @State private var dxvkInstall = NSHomeDirectory() + "/dxvk-release"
    @State private var dxvkInstall32 = NSHomeDirectory() + "/dxvk-release-32"
    @State private var steamSetup = NSHomeDirectory() + "/Downloads/SteamSetup.exe"
    @State private var mesaDir = NSHomeDirectory() + "/mesa/x64"
    @State private var dxmtDir = NSHomeDirectory() + "/dxmt"
    @State private var vkd3dDir = NSHomeDirectory() + "/vkd3d-proton"
    @State private var gptkDir: String = {
        // Try to find gptk dir next to the backend script
        let candidate = NSHomeDirectory() + "/macndcheese/gptk"
        return FileManager.default.fileExists(atPath: candidate) ? candidate : NSHomeDirectory() + "/gptk"
    }()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                PathRow(label: "DXVK source", path: $dxvkSrc, isDir: true)
                PathRow(label: "DXVK install (64-bit)", path: $dxvkInstall, isDir: true)
                PathRow(label: "DXVK install (32-bit)", path: $dxvkInstall32, isDir: true)
                PathRow(label: "SteamSetup.exe", path: $steamSetup, isDir: false)
                PathRow(label: "Mesa x64 dir", path: $mesaDir, isDir: true)
                PathRow(label: "DXMT dir", path: $dxmtDir, isDir: true)
                PathRow(label: "VKD3D-Proton dir", path: $vkd3dDir, isDir: true)
                PathRow(label: "GPTK dir", path: $gptkDir, isDir: true)
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
                Button("Browse") {
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

<<<<<<< discord-rpc
    // Current toggle selections
    @State private var installTools = false
    @State private var installWineStable = false
    @State private var installWineStaging = false
    @State private var installMesa = false
    @State private var buildDxvk = false
    @State private var installVkd3d = false
    @State private var installD3dMetal = false
    @State private var installGptkFull = false
    @State private var installRpcBridge = false

    // Baseline installed state (used to detect installs vs uninstalls)
    @State private var wasTools = false
    @State private var wasWineStable = false
    @State private var wasWineStaging = false
    @State private var wasMesa = false
    @State private var wasDxvk = false
    @State private var wasVkd3d = false
    @State private var wasD3dMetal = false
    @State private var wasGptkFull = false
    @State private var wasRpcBridge = false
=======
    // Toggle selections — each maps to one installer action
    @State private var wantTools = false
    @State private var wantWineStable = false
    @State private var wantWineStaging = false
    @State private var wantDxvk = false
    @State private var wantVkd3d = false
    @State private var wantGptkDlls = false
    @State private var wantDxmt = false
    @State private var wantMesa = false

    // Baseline installed state (used to detect installs vs uninstalls)
    @State private var hadTools = false
    @State private var hadWineStable = false
    @State private var hadWineStaging = false
    @State private var hadDxvk = false
    @State private var hadVkd3d = false
    @State private var hadGptkDlls = false
    @State private var hadDxmt = false
    @State private var hadMesa = false
>>>>>>> main

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
                GroupBox("Quick Setup") {
                    HStack(spacing: 12) {
                        Button("Minimal") {
                            wantTools = true; wantWineStable = true
                            wantDxvk = true; wantMesa = true
                        }
                        .buttonStyle(.bordered)
                        .help("Select: Tools, Wine Stable, DXVK, Mesa")
                        .disabled(isRunning)
                        Button("Everything") {
                            wantTools = true; wantWineStable = true; wantWineStaging = true
                            wantDxvk = true; wantVkd3d = true
                            wantGptkDlls = true; wantDxmt = true; wantMesa = true
                        }
                        .buttonStyle(.bordered)
                        .help("Select all components")
                        .disabled(isRunning)
                        Button("None") {
                            wantTools = false; wantWineStable = false; wantWineStaging = false
                            wantDxvk = false; wantVkd3d = false
                            wantGptkDlls = false; wantDxmt = false; wantMesa = false
                        }
                        .buttonStyle(.bordered)
                        .disabled(isRunning)
                    }
                    .padding(8)
                }

                GroupBox("Tools") {
                    VStack(alignment: .leading, spacing: 8) {
                        ComponentToggleRow("Tools (git, 7z, wget)", isOn: $wantTools,
                                          installed: hadTools, updateAvailable: toolsHasUpdate)
                            .disabled(isRunning)
                        ComponentToggleRow("Discord RPC Bridge", isOn: $installRpcBridge,
                                          installed: wasRpcBridge)
                            .disabled(isRunning)
                    }
                    .padding(8)
                }

                GroupBox("Wine (Translation Engine)") {
                    VStack(alignment: .leading, spacing: 8) {
                        ComponentToggleRow("Wine (Stable)", isOn: $wantWineStable,
                                          installed: hadWineStable, updateAvailable: wineStableHasUpdate)
                            .disabled(isRunning)
                        ComponentToggleRow(stagingLatestName.map { "Wine (Staging — \($0))" } ?? "Wine (Staging)",
                                          isOn: $wantWineStaging,
                                          installed: hadWineStaging, updateAvailable: wineStagingHasUpdate)
                            .disabled(isRunning)
                    }
                    .padding(8)
                }

                GroupBox("Graphics") {
                    VStack(alignment: .leading, spacing: 8) {
                        ComponentToggleRow(dxmtLatestName.map { "DXMT (\($0))" } ?? "DXMT",
                                          isOn: $wantDxmt, installed: hadDxmt, updateAvailable: dxmtHasUpdate)
                            .disabled(isRunning)
                        ComponentToggleRow("DXVK", isOn: $wantDxvk, installed: hadDxvk)
                            .disabled(isRunning)
                        ComponentToggleRow("VKD3D-Proton", isOn: $wantVkd3d, installed: hadVkd3d)
                            .disabled(isRunning)
                        ComponentToggleRow("GPTK DLLs (D3DMetal)", isOn: $wantGptkDlls, installed: hadGptkDlls)
                            .disabled(isRunning)
                        ComponentToggleRow("Mesa", isOn: $wantMesa, installed: hadMesa)
                            .disabled(isRunning)
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
                                 ? (installCurrentAction.isEmpty ? "Starting..." : installCurrentAction)
                                 : (installFailed ? "Finished with errors" : "Done!"))
                                .font(.caption)
                                .foregroundColor(isRunning ? .secondary : (installFailed ? .red : .green))
                            Spacer()
                            if installDone {
                                Button("Dismiss") { clearInstallState() }
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
                        Text("Checking components...")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Update") { runUpdate() }
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
<<<<<<< discord-rpc
                wasTools = status.hasTools;           installTools = status.hasTools
                wasWineStable = status.hasWineStable; installWineStable = status.hasWineStable
                wasWineStaging = status.hasWineStaging; installWineStaging = status.hasWineStaging
                wasMesa = status.hasMesa;             installMesa = status.hasMesa
                wasDxvk = status.hasDxvk64;           buildDxvk = status.hasDxvk64
                wasVkd3d = status.hasVkd3d;           installVkd3d = status.hasVkd3d
                wasD3dMetal = status.hasD3dMetal3;    installD3dMetal = status.hasD3dMetal3
                wasGptkFull = status.hasGptkFull;     installGptkFull = status.hasGptkFull
                wasRpcBridge = status.hasRpcBridge;   installRpcBridge = status.hasRpcBridge
=======
                hadTools = status.hasTools;             wantTools = status.hasTools
                hadWineStable = status.hasWineStable;   wantWineStable = status.hasWineStable
                hadWineStaging = status.hasWineStaging; wantWineStaging = status.hasWineStaging
                hadDxvk = status.hasDxvk64;             wantDxvk = status.hasDxvk64
                hadVkd3d = status.hasVkd3d;             wantVkd3d = status.hasVkd3d
                hadGptkDlls = status.hasGptkDlls;       wantGptkDlls = status.hasGptkDlls
                hadDxmt = status.hasDxmt;               wantDxmt = status.hasDxmt
                hadMesa = status.hasMesa;               wantMesa = status.hasMesa
>>>>>>> main
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
        let dxvkSrc = home + "/DXVK-macOS"
        let dxvkInstall64 = home + "/dxvk-release"
        let dxvkInstall32 = home + "/dxvk-release-32"
        let mesaDir = home + "/mesa/x64"
        let mesaUrl = "https://github.com/pal1000/mesa-dist-win/releases/download/23.1.9/mesa3d-23.1.9-release-msvc.7z"
        let dxmtDir = home + "/dxmt"
        let vkd3dDir = home + "/vkd3d-proton"

        // Plan actions: install if toggled on, uninstall if toggled off but was installed
        var uninstallActions: [String] = []
        var installActions: [String] = []
        func plan(_ on: Bool, _ was: Bool, install: String, uninstall: String) {
            if on { installActions.append(install) }
            else if was { uninstallActions.append(uninstall) }
        }
<<<<<<< discord-rpc
        plan(installTools,       wasTools,       install: "install_tools",        uninstall: "uninstall_tools")
        plan(installWineStable,  wasWineStable,  install: "install_wine",         uninstall: "uninstall_wine")
        plan(installWineStaging, wasWineStaging, install: "install_wine_staging", uninstall: "uninstall_wine_staging")
        plan(installMesa,        wasMesa,        install: "install_mesa",         uninstall: "uninstall_mesa")
        plan(buildDxvk,          wasDxvk,        install: "install_dxvk",         uninstall: "uninstall_dxvk")
        plan(installVkd3d,       wasVkd3d,       install: "install_vkd3d",        uninstall: "uninstall_vkd3d")
        plan(installD3dMetal,    wasD3dMetal,    install: "install_gptk_dlls",    uninstall: "uninstall_d3dmetal")
        plan(installGptkFull,    wasGptkFull,    install: "install_dxmt",         uninstall: "uninstall_dxmt")
        plan(installRpcBridge,   wasRpcBridge,   install: "install_rpc_bridge",   uninstall: "uninstall_rpc_bridge")
=======
        plan(wantTools,       hadTools,       install: "install_tools",        uninstall: "uninstall_tools")
        plan(wantWineStable,  hadWineStable,  install: "install_wine",         uninstall: "uninstall_wine")
        plan(wantWineStaging, hadWineStaging, install: "install_wine_staging", uninstall: "uninstall_wine_staging")
        plan(wantDxvk,        hadDxvk,        install: "install_dxvk",         uninstall: "uninstall_dxvk")
        plan(wantVkd3d,       hadVkd3d,       install: "install_vkd3d",        uninstall: "uninstall_vkd3d")
        plan(wantGptkDlls,    hadGptkDlls,    install: "install_gptk_dlls",    uninstall: "uninstall_gptk_dlls")
        plan(wantDxmt,        hadDxmt,        install: "install_dxmt",         uninstall: "uninstall_dxmt")
        plan(wantMesa,        hadMesa,        install: "install_mesa",         uninstall: "uninstall_mesa")
>>>>>>> main

        let allActions = uninstallActions + installActions
        guard !allActions.isEmpty else { return }

        clearInstallState()
        isRunning = true

        Task {
            guard let jobId = await backend.runInstaller(
                installerPath: installerPath,
                actions: allActions,
                prefix: prefix,
                dxvkSrc: dxvkSrc,
                dxvk64: dxvkInstall64,
                dxvk32: dxvkInstall32,
                mesa: mesaDir,
                mesaUrl: mesaUrl,
                dxmt: dxmtDir,
                vkd3d: vkd3dDir
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
                Text("Update available")
                    .font(.caption2)
                    .foregroundStyle(.yellow)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.yellow.opacity(0.15), in: Capsule())
            } else if installed {
                Text("Installed")
                    .font(.caption2)
                    .foregroundStyle(.green)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.green.opacity(0.15), in: Capsule())
            }
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
                Text("Wine Logs")
                    .font(.headline)

                Spacer()

                Button("Refresh") { scanLogs(); loadSelectedLog() }
                    .buttonStyle(.bordered)
                    .controlSize(.small)

                Button("Open Log Folder") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: logDir))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }

            // Log file picker
            if !logFiles.isEmpty {
                Picker("Log file:", selection: Binding(
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
                    Text(logText.isEmpty ? "No log content. Launch a game first." : logText)
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
                Toggle("Auto-refresh", isOn: $autoRefresh)
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
                logText = "... (\(lines.count - 500) lines truncated) ...\n" +
                    lines.suffix(500).joined(separator: "\n")
            } else {
                logText = content
            }
        } catch {
            logText = "Failed to read log: \(error.localizedDescription)"
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
