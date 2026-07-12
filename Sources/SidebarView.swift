import SwiftUI

struct SidebarView: View {
    @EnvironmentObject var backend: BackendClient
    @Binding var showCreateBottle: Bool
    @Binding var showStore: Bool
    @State private var confirmDelete: Bottle?
    @State private var confirmKill: Bottle?

    var body: some View {
        List(selection: Binding(
            get: { backend.activePrefix },
            set: { path in
                if let path { backend.selectBottle(path) }
            }
        )) {
            Section(L("Bottles")) {
                ForEach(backend.bottles) { bottle in
                    BottleRow(bottle: bottle)
                        .tag(bottle.path)
                        .contextMenu {
                            Button(L("Kill Wineserver")) {
                                confirmKill = bottle
                            }
                            Divider()
                            Button(L("Delete Bottle"), role: .destructive) {
                                confirmDelete = bottle
                            }
                        }
                }
                .onMove { from, to in
                    var paths = backend.bottles.map { $0.path }
                    paths.move(fromOffsets: from, toOffset: to)
                    Task { await backend.reorderBottles(paths: paths) }
                }
            }
        }
        .listStyle(.sidebar)
        .navigationTitle("MacNCheese")
        .safeAreaInset(edge: .bottom) {
            VStack(spacing: 8) {
                Button {
                    showStore = true
                } label: {
                    Label(L("Store"), systemImage: "storefront")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)

                Button {
                    showCreateBottle = true
                } label: {
                    Label(L("New Bottle"), systemImage: "plus")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
            .padding()
        }
        .alert(L("Kill Wineserver?"), isPresented: Binding(
            get: { confirmKill != nil },
            set: { if !$0 { confirmKill = nil } }
        )) {
            Button(L("Cancel"), role: .cancel) { confirmKill = nil }
            Button(L("Kill"), role: .destructive) {
                if let bottle = confirmKill {
                    Task { await backend.killWineserver(prefix: bottle.path) }
                }
                confirmKill = nil
            }
        } message: {
            if let bottle = confirmKill {
                Text(String(format: L("This will forcefully stop all Wine processes for \"%@\"."), bottle.name))
            }
        }
        .alert(L("Delete Bottle?"), isPresented: Binding(
            get: { confirmDelete != nil },
            set: { if !$0 { confirmDelete = nil } }
        )) {
            Button(L("Cancel"), role: .cancel) { confirmDelete = nil }
            Button(L("Delete"), role: .destructive) {
                if let bottle = confirmDelete {
                    Task { await backend.deleteBottle(path: bottle.path) }
                }
                confirmDelete = nil
            }
        } message: {
            if let bottle = confirmDelete {
                Text(String(format: L("This will permanently delete \"%@\" and all its contents."), bottle.name))
            }
        }
    }
}

struct BottleRow: View {
    @EnvironmentObject var backend: BackendClient
    let bottle: Bottle
    @State private var exeIcon: NSImage?
    // bottle.isReachable does a real FileManager syscall. Reading it directly
    // in `body` would re-run that on every re-render of this row — and
    // SwiftUI's ObservableObject invalidation is coarse: ANY @Published
    // change on backend re-renders every row in the sidebar, not just the
    // one whose data changed. Cache it and only recheck when it could
    // plausibly have changed: on first appearance, and when a drive
    // mounts/unmounts.
    @State private var isReachable = true

    var body: some View {
        HStack {
            Label {
                VStack(alignment: .leading, spacing: 2) {
                    Text(bottle.name)
                        .fontWeight(.medium)
                    Text(abbreviatePath(bottle.path))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            } icon: {
                if let icon = exeIcon {
                    Image(nsImage: icon)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(width: 22, height: 22)
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                } else if bottle.isEpicBottle {
                    EpicIcon(size: 22)
                } else if bottle.isSteamBottle {
                    Image(systemName: "gamecontroller.fill")
                        .foregroundStyle(.blue)
                } else {
                    Image(systemName: "wineglass")
                        .foregroundStyle(Color.brand)
                }
            }

            if !isReachable {
                Spacer(minLength: 4)
                Image(systemName: "externaldrive.badge.exclamationmark")
                    .foregroundStyle(.orange)
                    .help(L("This bottle's drive isn't connected."))
            }
        }
        .opacity(isReachable ? 1.0 : 0.85)
        .padding(.vertical, 2)
        .onAppear {
            isReachable = bottle.isReachable
            Task { await loadIcon() }
        }
        // The row's own .onAppear only fires once per mount, so if a drive
        // was disconnected when this row first loaded, the icon fetch was
        // skipped and never retried. volumeChangeTick bumps on every
        // mount/unmount; recheck reachability and retry the icon then.
        .onChange(of: backend.volumeChangeTick) { _ in
            isReachable = bottle.isReachable
            if exeIcon == nil { Task { await loadIcon() } }
        }
    }

    private func loadIcon() async {
        guard bottle.isReachable else { return }

        if let iconPath = bottle.iconPath, !iconPath.isEmpty,
           FileManager.default.fileExists(atPath: iconPath),
           let img = NSImage(contentsOfFile: iconPath) {
            exeIcon = img
            return
        }

        
        let exePath: String
        if let exe = bottle.launcherExe, !exe.isEmpty {
            exePath = exe
        } else if bottle.isSteamBottle {
            exePath = bottle.path + "/drive_c/Program Files (x86)/Steam/Steam.exe"
        } else {
            return
        }
        guard FileManager.default.fileExists(atPath: exePath) else { return }

       
        if let icoData = await backend.getExeIcon(exe: exePath),
           let img = NSImage(data: icoData) {
            exeIcon = img
            return
        }

        
        exeIcon = NSWorkspace.shared.icon(forFile: exePath)
    }

    private func abbreviatePath(_ path: String) -> String {
        path.replacingOccurrences(of: NSHomeDirectory(), with: "~")
    }
}