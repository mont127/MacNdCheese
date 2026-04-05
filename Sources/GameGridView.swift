import SwiftUI

struct GameGridView: View {
    @EnvironmentObject var backend: BackendClient
    let games: [Game]
    @Binding var searchText: String

    private var activeBottle: Bottle? {
        guard let prefix = backend.activePrefix else { return nil }
        return backend.bottles.first { $0.path == prefix }
    }

    private let columns = [
        GridItem(.adaptive(minimum: 160, maximum: 200), spacing: 16)
    ]

    var body: some View {
        VStack(spacing: 0) {
            // Top bar
            HStack {
                Text("Library")
                    .font(.title2)
                    .fontWeight(.bold)

                Spacer()

                if activeBottle?.isSteamBottle ?? true {
                    Button {
                        guard let prefix = backend.activePrefix else { return }
                        if backend.steamRunning {
                            Task {
                                await backend.killWineserver(prefix: prefix)
                                backend.steamRunning = false
                            }
                        } else {
                            Task { await backend.launchSteam(prefix: prefix) }
                        }
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: backend.steamRunning ? "stop.fill" : "play.fill")
                                .font(.caption)
                            Text(backend.steamRunning ? "Close Steam" : "Open Steam")
                        }
                    }
                    .buttonStyle(.bordered)
                    .tint(backend.steamRunning ? .red : .cyan)
                    .disabled(backend.activePrefix == nil)
                }

                HStack(spacing: 4) {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(.secondary)
                    TextField("Search games...", text: $searchText)
                        .textFieldStyle(.plain)
                        .frame(width: 200)
                }
                .padding(8)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 16)

            // Game grid
            ScrollView {
                LazyVGrid(columns: columns, spacing: 16) {
                    ForEach(games) { game in
                        GameCardView(game: game)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
            }
        }
    }
}

struct GameCardView: View {
    @EnvironmentObject var backend: BackendClient
    let game: Game
    @State private var isHovering = false
    @State private var showLaunchOptions = false
    @State private var coverImage: NSImage?

    var body: some View {
        VStack(spacing: 0) {
            // Cover image area
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(.ultraThinMaterial)
                    .frame(height: 220)

                if let image = coverImage {
                    Image(nsImage: image)
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .frame(height: 220)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: "gamecontroller.fill")
                            .font(.system(size: 32))
                            .foregroundStyle(.secondary)
                    }
                }

                // Hover overlay
                if isHovering {
                    RoundedRectangle(cornerRadius: 12)
                        .fill(.black.opacity(0.6))
                        .frame(height: 220)

                    VStack(spacing: 10) {
                        Button {
                            showLaunchOptions = true
                        } label: {
                            Label("Play", systemImage: "play.fill")
                                .font(.headline)
                                .frame(minWidth: 100)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.cyan)
                        .controlSize(.large)
                    }
                }
            }
            .frame(height: 220)

            // Game name
            Text(game.name)
                .font(.caption)
                .fontWeight(.medium)
                .lineLimit(2)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 8)
                .padding(.vertical, 8)
        }
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(
                    isHovering ? Color.cyan.opacity(0.5) : Color.white.opacity(0.1),
                    lineWidth: 1
                )
        )
        .scaleEffect(isHovering ? 1.02 : 1.0)
        .shadow(color: isHovering ? .cyan.opacity(0.2) : .clear, radius: 12)
        .animation(.easeOut(duration: 0.2), value: isHovering)
        .onHover { hovering in
            isHovering = hovering
        }
        .onAppear { loadCover() }
        .contextMenu {
            Button("Launch Options...") { showLaunchOptions = true }
            if let exe = game.exe {
                Button("Show in Finder") {
                    NSWorkspace.shared.selectFile(exe, inFileViewerRootedAtPath: "")
                }
            }
        }
        .sheet(isPresented: $showLaunchOptions) {
            GameLaunchSheet(game: game, coverImage: coverImage)
        }
    }

    private func loadCover() {
        guard let urlString = game.coverUrl,
              let url = URL(string: urlString) else { return }

        Task.detached(priority: .background) {
            do {
                let (data, _) = try await URLSession.shared.data(from: url)
                if let image = NSImage(data: data) {
                    await MainActor.run { coverImage = image }
                }
            } catch {
                // Cover not available, use placeholder
            }
        }
    }
}
