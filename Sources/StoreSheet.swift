import SwiftUI

private enum StoreTab: String, CaseIterable, Identifiable {
    case downloads = "Downloads"
    case discussions = "Discussions"
    case issues = "Issues"
    case pullRequests = "Pull Requests"
    case insights = "Insights"

    var id: String { rawValue }
    var systemImage: String {
        switch self {
        case .downloads: return "arrow.down.circle"
        case .discussions: return "bubble.left.and.bubble.right"
        case .issues: return "ladybug"
        case .pullRequests: return "arrow.triangle.pull"
        case .insights: return "chart.line.uptrend.xyaxis"
        }
    }
}

struct StoreView: View {
    @State private var selectedTab: StoreTab = .downloads

    var body: some View {
        VStack(spacing: 0) {
            Picker("", selection: $selectedTab) {
                ForEach(StoreTab.allCases) { tab in
                    Label(tab.rawValue, systemImage: tab.systemImage).tag(tab)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 12)

            Divider()

            Group {
                switch selectedTab {
                case .downloads:    DownloadsView()
                case .discussions:  GitHubListView(kind: .discussions)
                case .issues:       GitHubListView(kind: .issues)
                case .pullRequests: GitHubListView(kind: .pullRequests)
                case .insights:     InsightsView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}



private let storeRepo = "mont127/MacNdCheese"

@MainActor
private final class ReleaseStore: ObservableObject {
    @Published var versions: [String] = []
    @Published var selectedVersion: String = ""
    @Published var totalDownloads: Int? = nil
    @Published var perAssetCounts: [(name: String, count: Int)] = []
    @Published var releaseURL: String = ""
    @Published var publishedAt: String = ""
    @Published var loading = false
    @Published var error: String? = nil

    func loadReleaseList() {
        loading = true
        Task.detached(priority: .utility) {
            do {
                let url = URL(string: "https://api.github.com/repos/\(storeRepo)/releases?per_page=30")!
                var req = URLRequest(url: url)
                req.setValue("MacNCheese-Store", forHTTPHeaderField: "User-Agent")
                req.timeoutInterval = 12
                let (data, _) = try await URLSession.shared.data(for: req)
                guard let arr = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return }
                let tags = arr.compactMap { $0["tag_name"] as? String }
                await MainActor.run {
                    self.versions = tags
                    if self.selectedVersion.isEmpty, let first = tags.first {
                        self.selectedVersion = first
                        self.loadRelease(tag: first)
                    }
                    self.loading = false
                }
            } catch {
                await MainActor.run {
                    self.error = "Failed to load releases: \(error.localizedDescription)"
                    self.loading = false
                }
            }
        }
    }

    func loadRelease(tag: String) {
        loading = true
        error = nil
        Task.detached(priority: .utility) {
            do {
                let url = URL(string: "https://api.github.com/repos/\(storeRepo)/releases/tags/\(tag)")!
                var req = URLRequest(url: url)
                req.setValue("MacNCheese-Store", forHTTPHeaderField: "User-Agent")
                req.timeoutInterval = 12
                let (data, _) = try await URLSession.shared.data(for: req)
                guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
                let assets = (obj["assets"] as? [[String: Any]]) ?? []
                let perAsset = assets.compactMap { a -> (String, Int)? in
                    guard let name = a["name"] as? String,
                          let count = a["download_count"] as? Int else { return nil }
                    return (name, count)
                }
                let total = perAsset.reduce(0) { $0 + $1.1 }
                let html = (obj["html_url"] as? String) ?? "https://github.com/\(storeRepo)/releases/tag/\(tag)"
                let published = (obj["published_at"] as? String) ?? ""

                await MainActor.run {
                    self.totalDownloads = total
                    self.perAssetCounts = perAsset
                    self.releaseURL = html
                    self.publishedAt = published
                    self.loading = false
                }
            } catch {
                await MainActor.run {
                    self.error = "Failed to load release \(tag): \(error.localizedDescription)"
                    self.loading = false
                }
            }
        }
    }
}

private struct DownloadsView: View {
    @StateObject private var store = ReleaseStore()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Release")
                    .foregroundStyle(.secondary)
                Picker("", selection: Binding(
                    get: { store.selectedVersion },
                    set: { newTag in
                        store.selectedVersion = newTag
                        store.loadRelease(tag: newTag)
                    }
                )) {
                    ForEach(store.versions, id: \.self) { v in
                        Text(v).tag(v)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .disabled(store.versions.isEmpty)

                Spacer()

                if !store.releaseURL.isEmpty, let url = URL(string: store.releaseURL) {
                    Link("Open on GitHub", destination: url)
                        .font(.callout)
                }
            }

            if store.loading {
                HStack { ProgressView().controlSize(.small); Text("Loading...").foregroundStyle(.secondary) }
            } else if let err = store.error {
                Text(err).foregroundStyle(.red)
            } else if let total = store.totalDownloads {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text("\(total)")
                        .font(.system(size: 56, weight: .bold, design: .rounded))
                        .foregroundStyle(.tint)
                    VStack(alignment: .leading) {
                        Text("total downloads")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                        if !store.publishedAt.isEmpty {
                            Text("Published \(prettyDate(store.publishedAt))")
                                .font(.caption)
                                .foregroundStyle(.tertiary)
                        }
                    }
                }
                .padding(.vertical, 6)

                if !store.perAssetCounts.isEmpty {
                    Text("Per asset")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textCase(.uppercase)
                    ScrollView {
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(store.perAssetCounts, id: \.name) { item in
                                HStack {
                                    Text(item.name).font(.system(.callout, design: .monospaced))
                                    Spacer()
                                    Text("\(item.count)").foregroundStyle(.secondary)
                                }
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(.background.secondary, in: .rect(cornerRadius: 4))
                            }
                        }
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(20)
        .onAppear { if store.versions.isEmpty { store.loadReleaseList() } }
    }

    private func prettyDate(_ iso: String) -> String {
        let f = ISO8601DateFormatter()
        guard let d = f.date(from: iso) else { return iso }
        let o = DateFormatter()
        o.dateStyle = .medium
        return o.string(from: d)
    }
}



private enum GitHubKind {
    case discussions, issues, pullRequests
    var apiPath: String {
        switch self {
        case .discussions:  return "https://github.com/mont127/MacNdCheese/discussions.atom"
        case .issues:       return "https://api.github.com/repos/mont127/MacNdCheese/issues?state=open&per_page=20&sort=updated"
        case .pullRequests: return "https://api.github.com/repos/mont127/MacNdCheese/pulls?state=open&per_page=20&sort=updated"
        }
    }
    var pageURL: String {
        switch self {
        case .discussions:  return "https://github.com/mont127/MacNdCheese/discussions"
        case .issues:       return "https://github.com/mont127/MacNdCheese/issues"
        case .pullRequests: return "https://github.com/mont127/MacNdCheese/pulls"
        }
    }
    var emptyText: String {
        switch self {
        case .discussions:  return "No discussions"
        case .issues:       return "No open issues"
        case .pullRequests: return "No open pull requests"
        }
    }
}

private struct GitHubItem: Identifiable {
    let id: String
    let title: String
    let url: String
    let subtitle: String
}

@MainActor
private final class GitHubListStore: ObservableObject {
    @Published var items: [GitHubItem] = []
    @Published var loading = false
    @Published var error: String? = nil

    func load(_ kind: GitHubKind) {
        loading = true
        error = nil
        Task.detached(priority: .utility) {
            do {
                let url = URL(string: kind.apiPath)!
                var req = URLRequest(url: url)
                req.setValue("MacNCheese-Store", forHTTPHeaderField: "User-Agent")
                req.timeoutInterval = 12
                let (data, _) = try await URLSession.shared.data(for: req)
                let parsed: [GitHubItem]
                switch kind {
                case .discussions:
                    parsed = Self.parseAtom(String(data: data, encoding: .utf8) ?? "")
                case .issues, .pullRequests:
                    parsed = Self.parseJsonIssues(data, isPR: kind == .pullRequests)
                }
                await MainActor.run {
                    self.items = parsed
                    self.loading = false
                }
            } catch {
                await MainActor.run {
                    self.error = "Failed to load: \(error.localizedDescription)"
                    self.loading = false
                }
            }
        }
    }

    nonisolated private static func parseJsonIssues(_ data: Data, isPR: Bool) -> [GitHubItem] {
        guard let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { return [] }
        return arr.compactMap { obj in
            if !isPR, obj["pull_request"] != nil { return nil }
            guard let number = obj["number"] as? Int,
                  let title = obj["title"] as? String,
                  let html = obj["html_url"] as? String else { return nil }
            let user = (obj["user"] as? [String: Any])?["login"] as? String ?? ""
            let updated = (obj["updated_at"] as? String) ?? ""
            let comments = obj["comments"] as? Int ?? 0
            var sub = "#\(number) by \(user)"
            if !updated.isEmpty { sub += " · updated \(relativeDate(updated))" }
            if comments > 0 { sub += " · \(comments) comment\(comments == 1 ? "" : "s")" }
            return GitHubItem(id: html, title: title, url: html, subtitle: sub)
        }
    }

    nonisolated private static func parseAtom(_ xml: String) -> [GitHubItem] {
        var out: [GitHubItem] = []
        var cursor = xml.startIndex
        while let entryRange = xml.range(of: "<entry>", range: cursor..<xml.endIndex) {
            guard let endRange = xml.range(of: "</entry>", range: entryRange.upperBound..<xml.endIndex) else { break }
            let entry = String(xml[entryRange.upperBound..<endRange.lowerBound])
            let title = extractXMLTag(entry, "title")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let id = extractXMLTag(entry, "id")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let updated = extractXMLTag(entry, "updated")?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let link = extractAtomLink(entry) ?? ""
            let author = extractXMLTag(extractXMLTag(entry, "author") ?? "", "name") ?? ""
            if !title.isEmpty && !link.isEmpty {
                var sub = "by \(author)"
                if !updated.isEmpty { sub += " · updated \(relativeDate(updated))" }
                out.append(GitHubItem(id: id.isEmpty ? link : id, title: title, url: link, subtitle: sub))
            }
            cursor = endRange.upperBound
        }
        return out
    }

    nonisolated private static func extractXMLTag(_ xml: String, _ tag: String) -> String? {
        for opener in ["<\(tag)>", "<\(tag) "] {
            guard let r = xml.range(of: opener) else { continue }
            guard let openEnd = xml.range(of: ">", range: r.lowerBound..<xml.endIndex)?.upperBound else { continue }
            guard let closeStart = xml.range(of: "</\(tag)>", range: openEnd..<xml.endIndex)?.lowerBound else { continue }
            return String(xml[openEnd..<closeStart])
        }
        return nil
    }

    nonisolated private static func extractAtomLink(_ xml: String) -> String? {
        guard let r = xml.range(of: "<link") else { return nil }
        guard let end = xml.range(of: "/>", range: r.lowerBound..<xml.endIndex)?.upperBound else { return nil }
        let tag = String(xml[r.lowerBound..<end])
        guard let h = tag.range(of: "href=\"")?.upperBound,
              let q = tag.range(of: "\"", range: h..<tag.endIndex)?.lowerBound else { return nil }
        return String(tag[h..<q])
    }
}

nonisolated private func relativeDate(_ iso: String) -> String {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    guard let d = f.date(from: iso) else { return "" }
    let rel = RelativeDateTimeFormatter()
    rel.unitsStyle = .short
    return rel.localizedString(for: d, relativeTo: Date())
}

private struct GitHubListView: View {
    let kind: GitHubKind
    @StateObject private var store = GitHubListStore()

    var body: some View {
        VStack(spacing: 0) {
            if store.loading {
                Spacer()
                ProgressView()
                Spacer()
            } else if let err = store.error {
                Spacer()
                Text(err).foregroundStyle(.red).padding()
                Spacer()
            } else if store.items.isEmpty {
                Spacer()
                Text(kind.emptyText).foregroundStyle(.secondary)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(store.items) { item in
                            if let url = URL(string: item.url) {
                                Link(destination: url) {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(item.title).font(.callout).foregroundStyle(.primary)
                                        Text(item.subtitle).font(.caption).foregroundStyle(.secondary)
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(.horizontal, 16)
                                    .padding(.vertical, 10)
                                }
                                .buttonStyle(.plain)
                            }
                            Divider()
                        }
                    }
                }
            }

            HStack {
                Spacer()
                if let url = URL(string: kind.pageURL) {
                    Link("Open all on GitHub →", destination: url).font(.callout)
                }
            }
            .padding(12)
        }
        .onAppear { if store.items.isEmpty { store.load(kind) } }
    }
}



private struct InsightsView: View {
    var body: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "chart.line.uptrend.xyaxis").font(.system(size: 56)).foregroundStyle(.tint)
            Text("Insights & Traffic").font(.title3).fontWeight(.semibold)
            Text("GitHub's Insights and Traffic data require repo push access to view via API.\nOpen it on GitHub instead.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 40)
            if let url = URL(string: "https://github.com/mont127/MacNdCheese/graphs/traffic") {
                Link(destination: url) {
                    Label("Open Insights on GitHub", systemImage: "arrow.up.right.square")
                        .padding(.horizontal, 14)
                        .padding(.vertical, 8)
                }
                .buttonStyle(.borderedProminent)
            }
            Spacer()
        }
        .padding(20)
    }
}
