import SwiftUI

struct CreateBottleSheet: View {
    @EnvironmentObject var backend: BackendClient
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var launcherType = "steam"
    @State private var customPath = ""
    @State private var useCustomPath = false
    @State private var isCreating = false

    private var resolvedPath: String {
        if useCustomPath && !customPath.isEmpty {
            return customPath
        }
        let base = NSHomeDirectory() + "/Games/MacNCheese"
        let safeName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return safeName.isEmpty ? base : base + "/\(safeName)"
    }

    var body: some View {
        VStack(spacing: 20) {
            Text(L("Create a Bottle"))
                .font(.title2)
                .fontWeight(.bold)

            VStack(alignment: .leading, spacing: 6) {
                Text(L("Bottle Name"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                TextField(L("e.g. My Games"), text: $name)
                    .textFieldStyle(.roundedBorder)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text(L("Launcher"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Picker("", selection: $launcherType) {
                    Label("Steam", systemImage: "play.square.stack.fill").tag("steam")
                    Label { Text(L("Epic Games")) } icon: { EpicIcon(size: 16) }.tag("epic")
                    Label(L("None (plain Wine)"), systemImage: "wineglass").tag("custom")
                }
                .pickerStyle(.segmented)
                Text(launcherType == "steam"
                     ? L("Steam will be used to manage and launch games.")
                     : launcherType == "epic"
                     ? L("Epic Games library via Legendary. Connect your account after creation.")
                     : L("No launcher – add games manually."))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            VStack(alignment: .leading, spacing: 6) {
                Toggle(L("Custom location"), isOn: $useCustomPath)
                    .font(.caption)

                if useCustomPath {
                    HStack(spacing: 6) {
                        TextField(L("Path"), text: $customPath)
                            .textFieldStyle(.roundedBorder)
                            .font(.caption)
                        Button(L("Browse…")) {
                            let panel = NSOpenPanel()
                            panel.canChooseFiles = false
                            panel.canChooseDirectories = true
                            panel.canCreateDirectories = true
                            panel.prompt = L("Select")
                            if panel.runModal() == .OK, let url = panel.url {
                                customPath = url.path
                            }
                        }
                        .controlSize(.small)
                    }
                }

                Text(resolvedPath)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .lineLimit(2)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Spacer()

            HStack {
                Button(L("Cancel")) { dismiss() }
                    .keyboardShortcut(.cancelAction)

                Spacer()

                Button(L("Create")) {
                    guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
                    isCreating = true
                    Task {
                        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
                        let path = useCustomPath && !customPath.isEmpty ? customPath : nil
                        await backend.createBottle(name: trimmed, path: path, launcherType: launcherType)
                        isCreating = false
                        dismiss()
                    }
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isCreating)
            }
        }
        .padding(24)
        .frame(minWidth: 380, minHeight: 320)
        .background(Color(.windowBackgroundColor))
    }
}