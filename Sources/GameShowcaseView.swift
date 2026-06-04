import SwiftUI
import AppKit

// MARK: - Feed source
//
// The Game Showcase tab mirrors the rest of the Store (see StoreSheet.swift): it
// fetches a static JSON document directly over HTTPS via URLSession — the Python
// backend is not involved. The document is published by the Discord showcase bot
// (see discord-showcase-bot/) to the `showcase-data` branch of the store repo.

private let showcaseFeedURL =
    "https://raw.githubusercontent.com/mont127/MacNdCheese/showcase-data/showcase.json"

// MARK: - Decodable models (match showcase.json contract)

private struct ShowcaseFeed: Decodable {
    let channelName: String?
    let posts: [ShowcasePost]

    enum CodingKeys: String, CodingKey {
        case channelName = "channel_name"
        case posts
    }
}

private struct ShowcaseAuthor: Decodable {
    let name: String
    let avatarURL: String?

    enum CodingKeys: String, CodingKey {
        case name
        case avatarURL = "avatar_url"
    }
}

private struct ShowcasePost: Decodable, Identifiable {
    let id: String
    let title: String
    let author: ShowcaseAuthor?
    let createdAt: String?
    let tags: [String]?
    let body: String?
    let screenshots: [String]?
    let comments: [ShowcaseComment]?
    let url: String?

    enum CodingKeys: String, CodingKey {
        case id, title, author, tags, body, screenshots, comments, url
        case createdAt = "created_at"
    }
}

private struct ShowcaseComment: Decodable {
    let author: String?
    let avatarURL: String?
    let createdAt: String?
    let text: String?
    let images: [String]?

    enum CodingKeys: String, CodingKey {
        case author, text, images
        case avatarURL = "avatar_url"
        case createdAt = "created_at"
    }
}

// MARK: - Store

@MainActor
private final class ShowcaseStore: ObservableObject {
    @Published var posts: [ShowcasePost] = []
    @Published var channelName: String = ""
    @Published var loading = false
    @Published var error: String? = nil

    private var loadedOnce = false

    func loadIfNeeded() {
        guard !loadedOnce else { return }
        loadedOnce = true
        reload()
    }

    func reload() {
        loading = true
        error = nil
        Task.detached(priority: .utility) {
            do {
                // Bust raw.githubusercontent's CDN cache on a coarse (~5 min) bucket
                // so the feed stays reasonably fresh without hammering on every redraw.
                guard var comps = URLComponents(string: showcaseFeedURL) else { throw URLError(.badURL) }
                let bucket = Int(Date().timeIntervalSince1970 / 300)
                comps.queryItems = [URLQueryItem(name: "t", value: String(bucket))]
                guard let url = comps.url else { throw URLError(.badURL) }

                var req = URLRequest(url: url)
                req.setValue("MacNCheese-Store", forHTTPHeaderField: "User-Agent")
                req.cachePolicy = .reloadIgnoringLocalCacheData
                req.timeoutInterval = 12

                let (data, resp) = try await URLSession.shared.data(for: req)

                // No feed published yet (branch/file missing) -> show the empty state, not an error.
                if let http = resp as? HTTPURLResponse, http.statusCode == 404 {
                    await MainActor.run {
                        self.posts = []
                        self.channelName = ""
                        self.loading = false
                    }
                    return
                }

                let feed = try JSONDecoder().decode(ShowcaseFeed.self, from: data)
                await MainActor.run {
                    self.posts = feed.posts
                    self.channelName = feed.channelName ?? ""
                    self.loading = false
                }
            } catch {
                await MainActor.run {
                    self.error = "Failed to load showcase: \(error.localizedDescription)"
                    self.loading = false
                }
            }
        }
    }
}

// MARK: - View

struct GameShowcaseView: View {
    /// Text from the toolbar search field (shared with the game grid). Filters the feed.
    var searchText: String = ""
    @StateObject private var store = ShowcaseStore()

    private var filteredPosts: [ShowcasePost] {
        let q = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return store.posts }
        return store.posts.filter { post in
            post.title.localizedCaseInsensitiveContains(q)
                || (post.body?.localizedCaseInsensitiveContains(q) ?? false)
                || (post.author?.name.localizedCaseInsensitiveContains(q) ?? false)
                || (post.tags?.contains { $0.localizedCaseInsensitiveContains(q) } ?? false)
        }
    }

    var body: some View {
        Group {
            if store.loading && store.posts.isEmpty {
                centered { ProgressView(); Text(L("Loading showcase…")).foregroundStyle(.secondary).padding(.top, 8) }
            } else if let err = store.error, store.posts.isEmpty {
                centered {
                    Image(systemName: "wifi.exclamationmark").font(.system(size: 40)).foregroundStyle(.secondary)
                    Text(err).foregroundStyle(.red).multilineTextAlignment(.center).padding(.horizontal, 40)
                    Button(L("Retry")) { store.reload() }.buttonStyle(.bordered).padding(.top, 4)
                }
            } else if store.posts.isEmpty {
                centered {
                    Image(systemName: "gamecontroller").font(.system(size: 48)).foregroundStyle(.tint)
                    Text(L("No showcased games yet")).font(.title3).fontWeight(.semibold)
                    Text(L("Posts from the community Game Showcase channel will appear here."))
                        .foregroundStyle(.secondary).multilineTextAlignment(.center).padding(.horizontal, 40)
                }
            } else if filteredPosts.isEmpty {
                centered {
                    Image(systemName: "magnifyingglass").font(.system(size: 44)).foregroundStyle(.secondary)
                    Text(String(format: L("No games match \u{201C}%@\u{201D}"), searchText)).font(.title3).fontWeight(.semibold)
                    Text(L("Try a different search.")).foregroundStyle(.secondary)
                }
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        if !store.channelName.isEmpty {
                            Label("#\(store.channelName)", systemImage: "number")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        ForEach(filteredPosts) { post in
                            ShowcaseCard(post: post)
                        }
                    }
                    .padding(20)
                }
                .refreshable { store.reload() }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear { store.loadIfNeeded() }
    }

    @ViewBuilder private func centered<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(spacing: 10) {
            Spacer()
            content()
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Post card

private struct ShowcaseCard: View {
    let post: ShowcasePost
    @State private var showComments = false

    private var comments: [ShowcaseComment] { post.comments ?? [] }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Title + author / date
            VStack(alignment: .leading, spacing: 4) {
                Text(post.title).font(.title3).fontWeight(.bold)
                HStack(spacing: 4) {
                    if let a = post.author { Text(a.name) }
                    if let created = post.createdAt, !created.isEmpty {
                        Text("· \(showcaseRelativeDate(created))")
                    }
                }
                .font(.caption).foregroundStyle(.secondary)
            }

            // Tags
            if let tags = post.tags, !tags.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(tags, id: \.self) { tag in
                            Text(tag)
                                .font(.caption2)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(Color.accentColor.opacity(0.15), in: Capsule())
                                .foregroundStyle(.tint)
                        }
                    }
                }
            }

            // Body
            if let body = post.body, !body.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                Text(body)
                    .font(.callout)
                    .foregroundStyle(.primary.opacity(0.9))
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Screenshot gallery
            if let shots = post.screenshots, !shots.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(shots, id: \.self) { ShowcaseImage(urlString: $0, height: 200) }
                    }
                }
            }

            // Footer: comments toggle + Discord link
            HStack {
                if !comments.isEmpty {
                    Button {
                        withAnimation(.easeInOut(duration: 0.18)) { showComments.toggle() }
                    } label: {
                        Label(comments.count == 1
                                  ? String(format: L("%@ comment"), String(comments.count))
                                  : String(format: L("%@ comments"), String(comments.count)),
                              systemImage: showComments ? "chevron.down" : "chevron.right")
                            .font(.caption)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                }
                Spacer()
                if let urlStr = post.url, let url = URL(string: urlStr) {
                    Link(L("View on Discord →"), destination: url).font(.caption)
                }
            }

            if showComments && !comments.isEmpty {
                Divider()
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(Array(comments.enumerated()), id: \.offset) { _, comment in
                        ShowcaseCommentRow(comment: comment)
                    }
                }
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.primary.opacity(0.08), lineWidth: 1))
    }
}

// MARK: - Comment row

private struct ShowcaseCommentRow: View {
    let comment: ShowcaseComment

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            avatar
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(comment.author ?? L("Unknown")).font(.caption).fontWeight(.semibold)
                    if let created = comment.createdAt, !created.isEmpty {
                        Text(showcaseRelativeDate(created)).font(.caption2).foregroundStyle(.secondary)
                    }
                }
                if let text = comment.text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Text(text)
                        .font(.caption)
                        .foregroundStyle(.primary.opacity(0.9))
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let imgs = comment.images, !imgs.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(imgs, id: \.self) { ShowcaseImage(urlString: $0, height: 120) }
                        }
                    }
                }
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder private var avatar: some View {
        if let a = comment.avatarURL, let url = URL(string: a) {
            AsyncImage(url: url) { img in
                img.resizable().scaledToFill()
            } placeholder: {
                Circle().fill(Color.accentColor.opacity(0.2))
            }
            .frame(width: 22, height: 22)
            .clipShape(Circle())
        } else {
            Circle().fill(Color.accentColor.opacity(0.2)).frame(width: 22, height: 22)
        }
    }
}

// MARK: - Image tile (click to open full size)

private struct ShowcaseImage: View {
    let urlString: String
    let height: CGFloat

    private var tileWidth: CGFloat { height * 1.6 }

    var body: some View {
        if let url = URL(string: urlString) {
            AsyncImage(url: url) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().scaledToFill()
                case .failure:
                    placeholder(systemImage: "photo.badge.exclamationmark")
                case .empty:
                    ZStack {
                        placeholder(systemImage: "photo")
                        ProgressView().controlSize(.small)
                    }
                @unknown default:
                    placeholder(systemImage: "photo")
                }
            }
            .frame(width: tileWidth, height: height)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .contentShape(RoundedRectangle(cornerRadius: 8))
            .onTapGesture { NSWorkspace.shared.open(url) }
            .help(L("Open full image"))
        }
    }

    private func placeholder(systemImage: String) -> some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(.background.secondary)
            .frame(width: tileWidth, height: height)
            .overlay(Image(systemName: systemImage).font(.title3).foregroundStyle(.tertiary))
    }
}

// MARK: - Helpers

/// Parses Discord ISO-8601 timestamps (with or without fractional seconds) into a
/// short relative string, e.g. "3d ago". File-private to avoid clashing with the
/// `relativeDate` helper in StoreSheet.swift.
private func showcaseRelativeDate(_ iso: String) -> String {
    let withFractional = ISO8601DateFormatter()
    withFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    let plain = ISO8601DateFormatter()
    plain.formatOptions = [.withInternetDateTime]
    guard let date = withFractional.date(from: iso) ?? plain.date(from: iso) else { return "" }
    let formatter = RelativeDateTimeFormatter()
    formatter.unitsStyle = .abbreviated
    return formatter.localizedString(for: date, relativeTo: Date())
}
