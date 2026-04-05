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
            Text("Create a Bottle")
                .font(.title2)
                .fontWeight(.bold)

            VStack(alignment: .leading, spacing: 6) {
                Text("Bottle Name")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                TextField("e.g. My Games", text: $name)
                    .textFieldStyle(.roundedBorder)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("Launcher")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Picker("", selection: $launcherType) {
                    Label("Steam", systemImage: "play.square.stack.fill").tag("steam")
                    Label("None (plain Wine)", systemImage: "wineglass").tag("custom")
                }
                .pickerStyle(.segmented)
                Text(launcherType == "steam"
                     ? "Steam will be used to manage and launch games."
                     : "No launcher – add games manually.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            VStack(alignment: .leading, spacing: 6) {
                Toggle("Custom location", isOn: $useCustomPath)
                    .font(.caption)

                if useCustomPath {
                    HStack(spacing: 6) {
                        TextField("Path", text: $customPath)
                            .textFieldStyle(.roundedBorder)
                            .font(.caption)
                        Button("Browse") {
                            let panel = NSOpenPanel()
                            panel.canChooseFiles = false
                            panel.canChooseDirectories = true
                            panel.canCreateDirectories = true
                            panel.prompt = "Select"
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
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)

                Spacer()

                Button("Create") {
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
                .tint(.cyan)
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isCreating)
            }
        }
        .padding(24)
        .frame(width: 440, height: 380)
        .background(.ultraThinMaterial)
    }
}
