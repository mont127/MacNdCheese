import SwiftUI
import UniformTypeIdentifiers

enum InstallerPathStore {
    static let dxvkSrcKey = "installerPaths.dxvkSrc"
    static let dxvkInstallKey = "installerPaths.dxvkInstall"
    static let dxvkInstall32Key = "installerPaths.dxvkInstall32"
    static let steamSetupKey = "installerPaths.steamSetup"
    static let mesaDirKey = "installerPaths.mesaDir"
    static let dxmtDirKey = "installerPaths.dxmtDir"
    static let gptkDirKey = "installerPaths.gptkDir"

    static var defaultDXVKSrc: String { NSHomeDirectory() + "/DXVK-macOS" }
    static var defaultDXVKInstall: String { NSHomeDirectory() + "/dxvk-release" }
    static var defaultDXVKInstall32: String { NSHomeDirectory() + "/dxvk-release-32" }
    static var defaultSteamSetup: String { NSHomeDirectory() + "/Downloads/SteamSetup.exe" }
    static var defaultMesaDir: String { NSHomeDirectory() + "/mesa/x64" }
    static var defaultDXMTDir: String { NSHomeDirectory() + "/dxmt" }
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
            gptkDir: value(for: gptkDirKey, default: defaultGPTKDir)
        )
    }

    private static func value(for key: String, default defaultValue: String) -> String {
        guard let stored = UserDefaults.standard.string(forKey: key), !stored.isEmpty else {
            return defaultValue
        }
        return stored
    }

    /// The Mesa Windows build the installer pulls when "Mesa" is selected.
    static let mesaURL = "https://github.com/pal1000/mesa-dist-win/releases/download/23.1.9/mesa3d-23.1.9-release-msvc.7z"

    /// Locate the bundled installer.sh (next to the app Resources or the source
    /// repo). Returns nil when neither exists. Shared by the Setup tab and the
    /// first-run onboarding installer so both look in the same places.
    static func installerScriptPath() -> String? {
        let home = NSHomeDirectory()
        let resourcePath = Bundle.main.resourcePath ?? Bundle.main.bundlePath
        let candidates = [resourcePath + "/installer.sh", home + "/macndcheese/installer.sh"]
        return candidates.first { FileManager.default.fileExists(atPath: $0) }
    }
}

struct InstallerPaths {
    let dxvkSrc: String
    let dxvkInstall64: String
    let dxvkInstall32: String
    let steamSetup: String
    let mesaDir: String
    let dxmtDir: String
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
                case "bottle": BottleSettingsTab(selectedTab: $selectedTab)
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
        .tint(.brand)
    }
}




// MARK: - Bottle Tab

struct BottleSettingsTab: View {
    @EnvironmentObject var backend: BackendClient
    @Binding var selectedTab: String
    @State private var bottleName = ""
    @State private var launcherExe = ""
    @State private var iconPath = ""
    // the tuning flags below are opt-in and only shown while it is enabled.
    @State private var globalBackend = "d3dmetal3"
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

                    // The Wine selector and the "Unified Steam engine" switch are gone:
                    // the unified engine is the only one now, so there was nothing left to
                    // choose between and the switch's only remaining effect was to hide the
                    // backend picker below. Metal HUD and x87 JIT moved to the bottle's
                    // Applications section, which is what they govern; games carry their
                    // own copies in each game's detail view.
                    SettingsRow(label: L("Global game backend")) {
                        Picker("", selection: $globalBackend) {
                            Text("D3DMetal").tag("d3dmetal3")
                            Text("DXMT").tag("dxmt")
                            Text("DXVK").tag("dxvk")
                            Text("VR").tag("vr")
                        }
                        .pickerStyle(.segmented)
                        .onChange(of: globalBackend) { _ in saveBottleConfig() }
                    }

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
                            .tint(Color.brand)
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
        .onChange(of: backend.activePrefix) { _ in loadFields() }
    }

    private var isAppleSilicon: Bool {
        var value: Int32 = 0
        var size = MemoryLayout<Int32>.size
        if sysctlbyname("hw.optional.arm64", &value, &size, nil, 0) == 0 { return value == 1 }
        return false
    }


    private func loadFields() {
        if let bottle = activeBottle {
            bottleName = bottle.name
            launcherExe = bottle.launcherExe ?? ""
            iconPath = bottle.iconPath ?? ""
            Task {
                if let config = await backend.getBottleConfig(path: bottle.path) {
                    globalBackend = config["default_backend"] as? String ?? "d3dmetal3"
                }
            }
        }
    }

    private func saveBottleConfig() {
        guard let prefix = backend.activePrefix else { return }
        Task {
            var vals: [String: Any] = [
                "name": bottleName,
                "launcher_exe": launcherExe,
                "icon_path": iconPath,
                // Written unconditionally so a bottle left on "classic" migrates on save.
                "engine": "unified",
            ]
            vals["default_backend"] = globalBackend
            await backend.setBottleConfig(path: prefix, values: vals)
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
    @AppStorage(InstallerPathStore.gptkDirKey) private var gptkDir = InstallerPathStore.defaultGPTKDir

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                PathRow(label: L("DXVK source"), path: $dxvkSrc, isDir: true)
                PathRow(label: L("DXVK install (64-bit)"), path: $dxvkInstall, isDir: true)
                PathRow(label: L("DXVK install (32-bit)"), path: $dxvkInstall32, isDir: true)
                PathRow(label: L("SteamSetup.exe"), path: $steamSetup, isDir: false)
                PathRow(label: L("DXMT dir"), path: $dxmtDir, isDir: true)
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

// MARK: - Setup Tab (Packages)

/// Bradar one installable peice in the Setup tab. the tab got dropped in 164c82f; back then it
/// was 13 hand-wired toggle pairs. same job now but data-driven, and every package is whatever
/// does that job TODAY: VKD3D-Proton's DX12 -> D3DMetal, Wine Devel's OpenGL -> built into the
/// engine, the VC++/.NET redist installers -> the game runtimes pack.
struct SetupPackage: Identifiable {
    enum Kind {
        case engine, graphics, vr, tools, legacy
    }

    let id: String
    let kind: Kind
    let name: String
    let detail: String
    /// installer.sh action. nil = it ships with the engine, nothing to fetch
    let install: String?
    /// nil = install-only, installer.sh has no remover for it
    let uninstall: String?
    let installed: Bool
    var updateAvailable = false
    var recommended = false

    var isBuiltIn: Bool { install == nil }
    /// installed with no remover: the tick stays on, you can only reinstall it
    var isLocked: Bool { installed && uninstall == nil }
}

struct SetupSettingsTab: View {
    @EnvironmentObject var backend: BackendClient
    @StateObject private var installer = InstallRunner()
    @State private var status: ComponentsStatus?
    @State private var updates: UpdateInfo?
    @State private var selected: Set<String> = []
    @State private var isLoading = false
    @State private var showLegacy = false

    private var packages: [SetupPackage] {
        let s = status
        let u = updates
        var list: [SetupPackage] = []

        // Engine -------------------------------------------------------------
        if s?.needsRosetta == true {
            list.append(SetupPackage(
                id: "rosetta", kind: .engine, name: L("Rosetta 2"),
                detail: L("Apple's x86_64 translator. On Apple Silicon the engine and every Windows game run through it. Installed automatically when it is missing."),
                install: "install_rosetta", uninstall: nil,
                installed: s?.hasRosetta ?? false, recommended: true))
        }
        let engineName = s?.engineVersion.map { String(format: L("Wine engine %@ — Steam + games"), $0) }
            ?? L("Wine engine — Steam + games")
        if s?.engineBundled == true {
            // Bradar the engine lives in the signed .app. running install_wine_unified here would
            // recreate the deps copy reconcileEngines() deletes, n the two fight every launch
            list.append(SetupPackage(
                id: "engine", kind: .engine, name: engineName,
                detail: L("One patched Wine that draws Steam through DXMT and runs games on D3DMetal, DXMT, DXVK or OpenGL. It ships inside the app, so there is nothing to download."),
                install: nil, uninstall: nil, installed: true))
        } else {
            list.append(SetupPackage(
                id: "engine", kind: .engine, name: engineName,
                detail: L("One patched Wine that draws Steam through DXMT and runs games on D3DMetal, DXMT, DXVK or OpenGL. This copy of the app has no engine inside, so it is installed separately."),
                install: "install_wine_unified", uninstall: "uninstall_wine_unified",
                installed: s?.hasWineUnified ?? false, recommended: true))
        }
        list.append(SetupPackage(
            id: "libs", kind: .engine, name: L("Engine libraries — fonts, TLS, Vulkan, SDL"),
            detail: L("FreeType, gnutls, MoltenVK and SDL for the engine. Without them text can go missing and Steam can claim it is offline."),
            install: "stage_mnc_fonts", uninstall: nil,
            installed: s?.hasMncFonts ?? false, recommended: true))
        list.append(SetupPackage(
            id: "runtimes", kind: .engine, name: L("Game runtimes — .NET, HTML, shader compiler"),
            detail: L("wine-mono and wine-gecko for installers and launchers that need .NET or HTML, plus Microsoft's d3dcompiler_47 when the app ships it. Takes the place of running the VC++ and .NET redistributable installers."),
            install: "stage_redist", uninstall: nil,
            installed: s?.hasWineAddons ?? false, recommended: true))

        // Graphics -----------------------------------------------------------
        list.append(SetupPackage(
            id: "dxmt", kind: .graphics,
            name: u?.dxmtLatestName.map { String(format: L("DXMT (%@)"), $0) } ?? L("DXMT"),
            detail: L("Direct3D 10/11 on Metal. Draws the Steam window and runs games set to the DXMT backend."),
            install: "install_dxmt", uninstall: "uninstall_dxmt",
            installed: s?.hasDxmt ?? false,
            updateAvailable: u?.dxmtUpdateAvailable ?? false, recommended: true))
        list.append(SetupPackage(
            id: "dxvk", kind: .graphics, name: L("DXVK"),
            detail: L("Direct3D 9/10/11 on Vulkan through MoltenVK, for games set to the DXVK backend."),
            install: "install_dxvk", uninstall: "uninstall_dxvk",
            installed: s?.hasDxvk64 ?? false, recommended: true))
        list.append(SetupPackage(
            id: "d3dmetal", kind: .graphics, name: L("D3DMetal — DirectX 11/12"),
            detail: L("Direct3D 11/12 on Apple's D3DMetal, the default for games. Covers DirectX 12, which VKD3D-Proton used to do. Part of the engine."),
            install: nil, uninstall: nil, installed: s?.hasD3dMetal3 ?? false))
        list.append(SetupPackage(
            id: "opengl", kind: .graphics, name: L("OpenGL — SDL3 / OpenGL 3.2 games"),
            detail: L("OpenGL 3.2+ for SDL3 and OpenGL games such as Mewgenics. Replaces the separate Wine Devel download. Part of the engine."),
            install: nil, uninstall: nil, installed: s?.hasOpengl ?? false))

        // VR -----------------------------------------------------------------
        list.append(SetupPackage(
            id: "vr", kind: .vr, name: L("VR (OpenXR)"),
            detail: L("The wineopenxr bridge plus the x86_64 oxrsys runtime that streams to a Quest or Pico headset. Pick VR as the graphics backend afterwards."),
            install: "install_vr", uninstall: "uninstall_vr",
            installed: s?.hasVr ?? false))

        // Tools --------------------------------------------------------------
        list.append(SetupPackage(
            id: "tools", kind: .tools, name: L("Tools — 7-Zip, Git, Wget, Zstd"),
            detail: L("Used by the installers and Winetricks to download and unpack packages."),
            install: "install_tools", uninstall: nil,
            installed: s?.hasTools ?? false,
            updateAvailable: u?.toolsUpdateAvailable ?? false, recommended: true))

        // Legacy -------------------------------------------------------------
        list.append(SetupPackage(
            id: "wine_staging", kind: .legacy,
            name: u?.gcenxLatestName.map { String(format: L("Wine (Staging — %@)"), $0) } ?? L("Wine (Staging)"),
            detail: L("Standalone Gcenx Wine Staging. Only bottles set to it use it."),
            install: "install_wine_staging", uninstall: "uninstall_wine_staging",
            installed: s?.hasWineStaging ?? false,
            updateAvailable: u?.wineStagingUpdateAvailable ?? false))
        list.append(SetupPackage(
            id: "wine_stable", kind: .legacy, name: L("Wine (Stable)"),
            detail: L("The old standalone Wine. Only older bottles that still point at it need it."),
            install: "install_wine", uninstall: "uninstall_wine",
            installed: s?.hasWineStable ?? false,
            updateAvailable: u?.wineStableUpdateAvailable ?? false))
        list.append(SetupPackage(
            id: "gptk_dlls", kind: .legacy, name: L("GPTK DLL package"),
            detail: L("Apple's D3DMetal DLLs for the injection and GPTK backends that run outside the engine."),
            install: "install_gptk_dlls", uninstall: nil,
            installed: s?.hasGptkDlls ?? false))
        return list
    }

    /// Bradar install order: Rosetta first (nothing x86_64 runs without it), then tools becuse
    /// the other installers unpack with its 7z, then the rest as listed
    private var installOrdred: [SetupPackage] {
        let first = ["rosetta", "tools"]
        return first.compactMap { id in packages.first { $0.id == id } }
            + packages.filter { !first.contains($0.id) }
    }

    /// Bradar what Apply would run: uninstall what got unticked, install what got ticked or has
    /// an update, in installOrdred
    private var plannedChanges: (actions: [String], force: Bool) {
        let ordred = installOrdred
        var removes: [String] = []
        var adds: [String] = []
        var needsForse = false
        for pkg in ordred {
            let on = pkg.isLocked || selected.contains(pkg.id)
            if on, let action = pkg.install, !pkg.installed || pkg.updateAvailable {
                adds.append(action)
                // install_dxmt skips when DXMT is allready there, so an update has to force it
                if pkg.updateAvailable { needsForse = true }
            } else if !on, pkg.installed, let action = pkg.uninstall {
                removes.append(action)
            }
        }
        return (removes + adds, needsForse)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                GroupBox(L("Quick Setup")) {
                    HStack(spacing: 12) {
                        Button(L("Recommended")) {
                            selected.formUnion(packages.filter { $0.recommended && !$0.isBuiltIn }.map(\.id))
                        }
                        .buttonStyle(.bordered)
                        .help(L("Select Rosetta 2, the engine libraries, game runtimes, DXMT, DXVK and Tools"))
                        Button(L("Everything")) {
                            selected = Set(packages.filter { !$0.isBuiltIn }.map(\.id))
                            showLegacy = true
                        }
                        .buttonStyle(.bordered)
                        .help(L("Select all components"))
                        Button(L("None")) {
                            selected = Set(packages.filter { $0.isLocked }.map(\.id))
                        }
                        .buttonStyle(.bordered)
                        Spacer()
                    }
                    .disabled(installer.isRunning || isLoading)
                    .padding(8)
                }

                packageGroup(L("Engine"), .engine)
                packageGroup(L("Graphics"), .graphics)
                packageGroup(L("VR"), .vr)
                packageGroup(L("Tools"), .tools)

                GroupBox {
                    DisclosureGroup(isExpanded: $showLegacy) {
                        packageRows(.legacy)
                            .padding(.top, 8)
                    } label: {
                        Text(L("Legacy — only for older bottles"))
                    }
                    .padding(8)
                }

                if installer.isRunning || installer.done {
                    progressArea
                }

                HStack {
                    if isLoading {
                        ProgressView().controlSize(.small)
                        Text(L("Checking components..."))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(L("Reinstall Selected")) { runReinstall() }
                        .buttonStyle(.bordered)
                        .disabled(installer.isRunning || isLoading)
                        .help(L("Force-reinstall every selected component, even ones already installed"))
                    Button(L("Apply")) {
                        let plan = plannedChanges
                        start(plan.actions, force: plan.force)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Color.brand)
                    .disabled(installer.isRunning || isLoading || plannedChanges.actions.isEmpty)
                    .help(L("Install what is ticked and remove what was unticked"))
                }
            }
            .padding(20)
        }
        .onAppear { loadStatus() }
        .onChange(of: installer.done) { done in
            if done { loadStatus() }
        }
    }

    private func packageGroup(_ title: String, _ kind: SetupPackage.Kind) -> some View {
        GroupBox(title) {
            packageRows(kind)
                .padding(8)
        }
    }

    private func packageRows(_ kind: SetupPackage.Kind) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(packages.filter { $0.kind == kind }) { pkg in
                SetupPackageRow(package: pkg, isOn: selection(for: pkg))
                    .disabled(installer.isRunning || isLoading)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func selection(for pkg: SetupPackage) -> Binding<Bool> {
        Binding(
            get: { pkg.isLocked || selected.contains(pkg.id) },
            set: { on in
                if on {
                    selected.insert(pkg.id)
                } else if !pkg.isLocked {
                    selected.remove(pkg.id)
                }
            })
    }

    private var progressArea: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                if installer.isRunning {
                    ProgressView().controlSize(.small)
                } else if installer.failed {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
                } else {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                }
                Text(installer.isRunning
                     ? (installer.currentAction.isEmpty ? L("Starting...") : installer.currentAction)
                     : (installer.failed ? L("Finished with errors") : L("Done!")))
                    .font(.caption)
                    .foregroundColor(installer.isRunning ? .secondary : (installer.failed ? .red : .green))
                Spacer()
                if installer.done && !installer.isRunning {
                    Button(L("Dismiss")) { installer.reset() }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
            }

            ScrollViewReader { proxy in
                ScrollView {
                    Text(installer.logLines.joined(separator: "\n"))
                        .font(.system(.caption2, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .id("setupLogBottom")
                }
                .frame(height: 140)
                .background(.black.opacity(0.25))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .onChange(of: installer.logLines) { _ in
                    proxy.scrollTo("setupLogBottom", anchor: .bottom)
                }
            }
        }
    }

    private func runReinstall() {
        let actions = installOrdred
            .filter { $0.isLocked || selected.contains($0.id) }
            .compactMap(\.install)
        start(actions, force: true)
    }

    private func start(_ actions: [String], force: Bool) {
        guard !actions.isEmpty else { return }
        Task { await installer.run(actions: actions, backend: backend, force: force) }
    }

    private func loadStatus() {
        isLoading = true
        Task {
            if let s = await backend.getComponentsStatus() {
                status = s
                // Bradar tick what is on disk, so Apply starts out as a no-op
                selected = Set(packages.filter { !$0.isBuiltIn && $0.installed }.map(\.id))
            }
            isLoading = false
            if let u = await backend.getUpdateInfo() {
                updates = u
            }
        }
    }
}

struct SetupPackageRow: View {
    let package: SetupPackage
    @Binding var isOn: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            if package.isBuiltIn {
                Image(systemName: package.installed ? "checkmark.circle.fill" : "circle.dashed")
                    .foregroundStyle(package.installed ? Color.green : Color.secondary)
                labels
            } else {
                Toggle(isOn: $isOn) { labels }
                    .disabled(package.isLocked)
            }
            Spacer(minLength: 8)
            badge
        }
    }

    private var labels: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(package.name)
            Text(package.detail)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var badge: some View {
        if package.updateAvailable {
            pill(L("Update available"), .yellow)
        } else if package.isBuiltIn {
            pill(package.installed ? L("Built in") : L("Needs the engine"),
                 package.installed ? .green : .secondary)
        } else if package.installed {
            pill(L("Installed"), .green)
        }
    }

    private func pill(_ text: String, _ color: Color) -> some View {
        Text(text)
            .font(.caption2)
            .foregroundStyle(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15), in: Capsule())
            .fixedSize()
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
    @AppStorage(UpdateChecker.autoInstallKey) private var autoInstallUpdates = false
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
    // What happens to running Wine when the app quits: "ask" | "kill" | "leave".
    // Also settable from the quit alert's "Remember my choice" checkbox.
    @AppStorage("quit_wine_behavior") private var quitWineBehavior = "ask"
    // Power saver: stop the background (silent) Steam a few minutes after the
    // last game exits — its CEF stack burns CPU/battery while idle.
    @AppStorage("auto_stop_steam") private var autoStopSteam = true

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
                    .tint(Color.brand)
                    .disabled(isDiagnosing || isRepairing)
                }

                // One-click fix for the most common support case: Steam won't
                // start. Re-downloads the latest MacNCheese Wine and re-runs
                // wineboot on the active bottle (existing repair-job pipeline).
                GroupBox {
                    HStack(alignment: .center, spacing: 12) {
                        Image(systemName: "wrench.and.screwdriver.fill")
                            .font(.title2)
                            .foregroundStyle(.orange)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(L("Steam not launching? Run this simple fix!"))
                                .fontWeight(.semibold)
                            Text(L("Downloads the latest MacNCheese Wine and re-runs wineboot on this bottle."))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button {
                            pendingRepair = CheeseRepairAction(
                                id: "steam_simple_fix",
                                title: L("Steam not launching? Run this simple fix!"),
                                details: L("Downloads the latest MacNCheese Wine and re-runs wineboot on this bottle."),
                                destructive: false,
                                recommended: true
                            )
                        } label: {
                            Label(L("Run Fix"), systemImage: "bolt.fill")
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.orange)
                        .disabled(isDiagnosing || isRepairing)
                    }
                    .padding(6)
                }

                GroupBox(L("Updates")) {
                    VStack(alignment: .leading, spacing: 6) {
                        Toggle(L("Install updates automatically"), isOn: $autoInstallUpdates)
                        Text(L("Off: new versions show a banner and wait for you. On: MacNCheese downloads the update and restarts itself on launch."))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(6)
                }

                GroupBox(L("On Quit")) {
                    VStack(alignment: .leading, spacing: 6) {
                        Picker("", selection: $quitWineBehavior) {
                            Text(L("Ask every time")).tag("ask")
                            Text(L("Quit all Wine processes")).tag("kill")
                            Text(L("Leave Wine running")).tag("leave")
                        }
                        .pickerStyle(.segmented)
                        .labelsHidden()
                        Text(L("What happens to running games and Wine when you close MacNCheese."))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(6)
                }

                GroupBox(L("Power")) {
                    VStack(alignment: .leading, spacing: 6) {
                        Toggle(L("Stop background Steam when no game is running"), isOn: $autoStopSteam)
                        Text(L("Background Steam keeps using CPU after games quit; this stops it after 5 idle minutes. Steam you opened yourself is never touched."))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(6)
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
                .onChange(of: repairLogLines) { _ in
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
                .foregroundStyle(repair.destructive ? .orange : Color.brand)
                .frame(width: 22)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(repair.title)
                        .fontWeight(.medium)
                    if repair.recommended {
                        Text(L("Recommended"))
                            .font(.caption2)
                            .foregroundStyle(Color.brand)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.brand.opacity(0.14), in: Capsule())
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
                .onChange(of: logText) { _ in
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

// MARK: - Wine Selector

/// Detection-driven Wine picker for the Bottle tab. Shows Automatic / Stable /
/// Staging, each annotated with whether that build is actually installed and its
/// real `wine --version`. Builds that aren't installed can't be selected and
/// offer a shortcut to the Setup tab to install them.

private struct WineOptionRow: View {
    let title: String
    let subtitle: String?
    let installed: Bool
    let version: String?
    let selectable: Bool
    let isSelected: Bool
    let onSelect: () -> Void
    let onInstall: (() -> Void)?

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: isSelected ? "largecircle.fill.circle" : "circle")
                .font(.system(size: 16))
                .foregroundStyle(isSelected ? Color.accentColor : Color.secondary)

            VStack(alignment: .leading, spacing: 1) {
                Text(title).fontWeight(.medium)
                if let subtitle {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                } else if let version, installed {
                    Text(version)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }

            Spacer()

            if let onInstall, !installed {
                Text(L("Not installed"))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Button(L("Install")) { onInstall() }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            } else if installed {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .help(L("Installed"))
            }
        }
        .padding(.vertical, 9)
        .padding(.horizontal, 12)
        .contentShape(Rectangle())
        .onTapGesture { if selectable { onSelect() } }
        .opacity(selectable ? 1 : 0.5)
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
                .fontWeight(.semibold)
                .foregroundStyle(.secondary)
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
