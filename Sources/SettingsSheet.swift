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
        }
    }

    private func saveBottleConfig() {
        guard let prefix = backend.activePrefix else { return }
        Task {
            await backend.setBottleConfig(path: prefix, values: [
                "name": bottleName,
                "launcher_exe": launcherExe,
                "icon_path": iconPath,
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
    @State private var runningAction = ""

    @State private var installTools = false
    @State private var installWine = false
    @State private var installMesa = false
    @State private var buildDxvk = false
    @State private var buildDxvk32 = false
    @State private var installGptkFull = false
    @State private var installD3dMetal3 = false
    @State private var importGptkDlls = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                // Quick Setup
                GroupBox("Quick Setup") {
                    HStack(spacing: 12) {
                        Button("Minimal") {
                            installTools = true; installWine = true
                            buildDxvk = true; buildDxvk32 = true; installMesa = true
                        }
                        .buttonStyle(.bordered)
                        .help("Select: Tools, Wine, DXVK (64/32), Mesa")

                        Button("Everything") {
                            installTools = true; installWine = true
                            installMesa = true; buildDxvk = true; buildDxvk32 = true
                            installGptkFull = true; installD3dMetal3 = true; importGptkDlls = true
                        }
                        .buttonStyle(.bordered)
                        .help("Select all components")
                    }
                    .padding(8)
                }

                // Components
                GroupBox("Components") {
                    VStack(alignment: .leading, spacing: 8) {
                        Toggle("Install Tools (git, 7z, winetricks)", isOn: $installTools)
                        Toggle("Install Wine", isOn: $installWine)
                        Toggle("Install Mesa", isOn: $installMesa)
                        Toggle("Install DXVK (64-bit)", isOn: $buildDxvk)
                        Toggle("Install DXVK (32-bit)", isOn: $buildDxvk32)

                        Divider()

                        Toggle("Install GPTK Full (Experimental)", isOn: $installGptkFull)
                            .foregroundStyle(.yellow)
                            .fontWeight(.semibold)
                        Toggle("Install D3DMetal 3 (Prebuilt)", isOn: $installD3dMetal3)
                            .foregroundStyle(.cyan)
                            .fontWeight(.semibold)
                        Toggle("Import GPTK DLLs", isOn: $importGptkDlls)
                            .foregroundStyle(.blue)
                            .fontWeight(.semibold)
                    }
                    .padding(8)
                }

                // Install button
                HStack {
                    if isRunning {
                        ProgressView()
                            .controlSize(.small)
                        Text("Running: \(runningAction)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()

                    Button("Install Selected") {
                        runInstall()
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.cyan)
                    .disabled(isRunning || !anySelected)
                }

                // Status
                GroupBox("System Status") {
                    VStack(alignment: .leading, spacing: 8) {
                        StatusRow(label: "Wine",
                                  isAvailable: backend.status?.wineFound ?? false,
                                  detail: backend.status?.winePath)
                        StatusRow(label: "DXVK",
                                  isAvailable: backend.status?.hasDxvk ?? false)
                        StatusRow(label: "Mesa",
                                  isAvailable: backend.status?.hasMesa ?? false)
                    }
                    .padding(8)
                }
            }
            .padding(20)
        }
    }

    private var anySelected: Bool {
        installTools || installWine || installMesa || buildDxvk || buildDxvk32
        || installGptkFull || installD3dMetal3 || importGptkDlls
    }

    private func runInstall() {
        isRunning = true
        runningAction = "Preparing..."

        let home = NSHomeDirectory()
        let resourcePath = Bundle.main.resourcePath ?? Bundle.main.bundlePath
        let candidates = [
            resourcePath + "/installer.sh",
            home + "/macndcheese/installer.sh",
        ]
        guard let installerPath = candidates.first(where: { FileManager.default.fileExists(atPath: $0) }) else {
            runningAction = "installer.sh not found"
            isRunning = false
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

        var actions: [String] = []
        if installTools { actions.append("install_tools") }
        if installWine { actions.append("install_wine") }
        if buildDxvk || buildDxvk32 { actions.append("install_dxvk") }
        if installMesa { actions.append("install_mesa") }
        if installGptkFull { actions.append("install_dxmt") }
        if installD3dMetal3 { actions.append("install_vkd3d") }
        if importGptkDlls { actions.append("quick_setup") }

        if actions.isEmpty {
            runningAction = "No actions selected"
            isRunning = false
            return
        }

        // Write a temp shell script to avoid AppleScript escaping issues
        let tmpScript = NSTemporaryDirectory() + "macncheese_install.sh"
        var lines: [String] = [
            "#!/bin/sh",
            "set -e",
            "export MNC_SUDOLESS=1",
            "INSTALLER='\(installerPath)'",
            "PREFIX='\(prefix)'",
            "DXVK_SRC='\(dxvkSrc)'",
            "DXVK64='\(dxvkInstall64)'",
            "DXVK32='\(dxvkInstall32)'",
            "MESA='\(mesaDir)'",
            "MESA_URL='\(mesaUrl)'",
            "DXMT='\(dxmtDir)'",
            "VKD3D='\(vkd3dDir)'",
            "",
        ]
        for action in actions {
            lines.append("echo '=== Running: \(action) ==='")
            lines.append("\"$INSTALLER\" \(action) \"$PREFIX\" \"$DXVK_SRC\" \"$DXVK64\" \"$DXVK32\" \"$MESA\" \"$MESA_URL\" \"$DXMT\" \"\" \"$VKD3D\"")
            lines.append("")
        }
        lines.append("echo ''")
        lines.append("echo '=== All done! You can close this window. ==='")
        lines.append("read -p 'Press Enter to close...'")

        do {
            try lines.joined(separator: "\n").write(toFile: tmpScript, atomically: true, encoding: .utf8)
            // Make executable
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: tmpScript)
        } catch {
            runningAction = "Failed to write install script: \(error.localizedDescription)"
            isRunning = false
            return
        }

        // Open in Terminal.app
        let appleScript = "tell application \"Terminal\"\nactivate\ndo script \"\(tmpScript)\"\nend tell"

        runningAction = "Running in Terminal..."
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        task.arguments = ["-e", appleScript]
        do {
            try task.run()
        } catch {
            runningAction = "Failed to open Terminal: \(error.localizedDescription)"
        }

        DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
            isRunning = false
            runningAction = ""
            Task { await backend.loadStatus() }
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
                    NSWorkspace.shared.open(URL(fileURLWithPath: NSHomeDirectory()))
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

    private func scanLogs() {
        let home = NSHomeDirectory()
        let fm = FileManager.default
        do {
            let files = try fm.contentsOfDirectory(atPath: home)
            logFiles = files
                .filter { $0.hasSuffix("-wine.log") }
                .sorted { lhs, rhs in
                    // Sort by modification date, newest first
                    let lDate = (try? fm.attributesOfItem(atPath: home + "/" + lhs)[.modificationDate] as? Date) ?? .distantPast
                    let rDate = (try? fm.attributesOfItem(atPath: home + "/" + rhs)[.modificationDate] as? Date) ?? .distantPast
                    return lDate > rDate
                }
                .map { (name: $0, path: home + "/" + $0) }

            // Also check ~/dxvk-logs
            let dxvkLogDir = home + "/dxvk-logs"
            if fm.fileExists(atPath: dxvkLogDir) {
                let dxvkFiles = try fm.contentsOfDirectory(atPath: dxvkLogDir)
                    .filter { $0.hasSuffix(".log") }
                    .sorted()
                    .map { (name: "dxvk-logs/" + $0, path: dxvkLogDir + "/" + $0) }
                logFiles.append(contentsOf: dxvkFiles)
            }
        } catch {
            // ignore
        }

        // Auto-select most recent if nothing selected
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

struct StatusRow: View {
    let label: String
    let isAvailable: Bool
    var detail: String?

    var body: some View {
        HStack {
            Image(systemName: isAvailable ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(isAvailable ? .green : .red)
            Text(label)
                .fontWeight(.medium)
            Spacer()
            if let detail, !detail.isEmpty {
                Text(URL(fileURLWithPath: detail).lastPathComponent)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                Text(isAvailable ? "Installed" : "Not Found")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
