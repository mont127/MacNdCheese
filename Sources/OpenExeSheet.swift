import SwiftUI
import AppKit
import CoreServices

/// A request to open a Windows executable handed to the app by Finder.
struct OpenExeRequest: Identifiable {
    let id = UUID()
    let url: URL
}

/// Sheet shown when the user double-clicks a .exe/.msi in Finder. Lets them
/// pick which bottle to run/install it in (or create a new one).
struct OpenExeSheet: View {
    @EnvironmentObject var backend: BackendClient
    @Environment(\.dismiss) private var dismiss

    let url: URL

    @State private var selectedBottlePath: String = ""
    @State private var icon: NSImage?
    @State private var isRunning = false
    @State private var showCreateBottle = false
    @State private var knownPathsBeforeCreate: Set<String> = []
    @State private var showMakeDefaultPrompt = false

    private var fileName: String { url.lastPathComponent }
    private var isInstaller: Bool {
        let lower = fileName.lowercased()
        return lower.hasSuffix(".msi") || lower.contains("setup") || lower.contains("install")
    }
    private var runVerb: String { isInstaller ? "Install" : "Run" }

    var body: some View {
        VStack(spacing: 20) {
            VStack(spacing: 12) {
                Group {
                    if let icon {
                        Image(nsImage: icon).resizable().interpolation(.high)
                    } else {
                        Image(systemName: "app.dashed").resizable()
                            .foregroundStyle(.secondary)
                    }
                }
                .scaledToFit()
                .frame(width: 64, height: 64)

                Text(fileName)
                    .font(.headline)
                    .lineLimit(2)
                    .multilineTextAlignment(.center)

                Text("Choose a bottle to \(runVerb.lowercased()) this Windows program in.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }

            if backend.bottles.isEmpty {
                Text("You don't have any bottles yet. Create one to continue.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Bottle")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker("", selection: $selectedBottlePath) {
                        ForEach(backend.bottles) { bottle in
                            Text(bottle.name).tag(bottle.path)
                        }
                    }
                    .labelsHidden()
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }

            Button {
                knownPathsBeforeCreate = Set(backend.bottles.map { $0.path })
                showCreateBottle = true
            } label: {
                Label("New Bottle…", systemImage: "plus")
            }
            .buttonStyle(.bordered)

            Spacer(minLength: 0)

            HStack {
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)

                Spacer()

                Button {
                    runInChosenBottle()
                } label: {
                    if isRunning {
                        ProgressView().controlSize(.small)
                    } else {
                        Text(runVerb)
                    }
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
                .disabled(selectedBottlePath.isEmpty || isRunning)
            }
        }
        .padding(24)
        .frame(minWidth: 380, minHeight: 360)
        .background(Color(.windowBackgroundColor))
        .onAppear {
            selectDefaultBottle()
            loadIcon()
        }
        .onChange(of: backend.bottles) { _ in
            // A new bottle was created via the nested sheet – select it.
            if let newPath = backend.bottles.map({ $0.path })
                .first(where: { !knownPathsBeforeCreate.contains($0) }) {
                selectedBottlePath = newPath
            } else if selectedBottlePath.isEmpty {
                selectDefaultBottle()
            }
        }
        .sheet(isPresented: $showCreateBottle) {
            CreateBottleSheet()
        }
        .alert("Open Windows programs with MacNCheese?", isPresented: $showMakeDefaultPrompt) {
            Button("Set as Default") {
                setAsDefaultHandler()
                dismiss()
            }
            Button("Not Now", role: .cancel) { dismiss() }
        } message: {
            Text("You can double-click .exe and .msi files to run them in a bottle. You can change this later in Finder.")
        }
    }

    private func selectDefaultBottle() {
        guard selectedBottlePath.isEmpty else { return }
        if let active = backend.activePrefix,
           backend.bottles.contains(where: { $0.path == active }) {
            selectedBottlePath = active
        } else {
            selectedBottlePath = backend.bottles.first?.path ?? ""
        }
    }

    private func loadIcon() {
        Task {
            if let data = await backend.getExeIcon(exe: url.path),
               let image = NSImage(data: data) {
                icon = image
            } else {
                icon = NSWorkspace.shared.icon(forFile: url.path)
            }
        }
    }

    private func runInChosenBottle() {
        guard !selectedBottlePath.isEmpty else { return }
        isRunning = true
        let prefix = selectedBottlePath
        Task {
            await backend.runExe(prefix: prefix, exe: url.path)
            isRunning = false
            // Offer to become the default handler the first time only.
            if !UserDefaults.standard.bool(forKey: "askedDefaultExeHandler") {
                UserDefaults.standard.set(true, forKey: "askedDefaultExeHandler")
                showMakeDefaultPrompt = true
            } else {
                dismiss()
            }
        }
    }

    private func setAsDefaultHandler() {
        let bundleID = (Bundle.main.bundleIdentifier ?? "com.marcel.macncheese") as CFString
        for uti in ["com.microsoft.windows-executable", "com.microsoft.windows-installer"] {
            LSSetDefaultRoleHandlerForContentType(uti as CFString, .all, bundleID)
        }
    }
}
