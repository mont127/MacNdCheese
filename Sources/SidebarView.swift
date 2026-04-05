import SwiftUI

struct SidebarView: View {
    @EnvironmentObject var backend: BackendClient
    @Binding var showCreateBottle: Bool
    @State private var confirmDelete: Bottle?

    var body: some View {
        List(selection: Binding(
            get: { backend.activePrefix },
            set: { path in
                if let path { backend.selectBottle(path) }
            }
        )) {
            Section("Bottles") {
                ForEach(backend.bottles) { bottle in
                    BottleRow(bottle: bottle)
                        .tag(bottle.path)
                        .contextMenu {
                            Button("Kill Wineserver") {
                                Task { await backend.killWineserver(prefix: bottle.path) }
                            }
                            Divider()
                            Button("Delete Bottle", role: .destructive) {
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
            Button {
                showCreateBottle = true
            } label: {
                Label("New Bottle", systemImage: "plus")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .controlSize(.large)
            .padding()
        }
        .alert("Delete Bottle?", isPresented: Binding(
            get: { confirmDelete != nil },
            set: { if !$0 { confirmDelete = nil } }
        )) {
            Button("Cancel", role: .cancel) { confirmDelete = nil }
            Button("Delete", role: .destructive) {
                if let bottle = confirmDelete {
                    Task { await backend.deleteBottle(path: bottle.path) }
                }
                confirmDelete = nil
            }
        } message: {
            if let bottle = confirmDelete {
                Text("This will permanently delete \"\(bottle.name)\" and all its contents.")
            }
        }
    }
}

struct BottleRow: View {
    let bottle: Bottle

    var body: some View {
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
            if bottle.isSteamBottle {
                Image(systemName: "play.square.stack.fill")
                    .foregroundStyle(.blue)
            } else {
                Image(systemName: "wineglass")
                    .foregroundStyle(.cyan)
            }
        }
        .padding(.vertical, 2)
    }

    private func abbreviatePath(_ path: String) -> String {
        path.replacingOccurrences(of: NSHomeDirectory(), with: "~")
    }
}
