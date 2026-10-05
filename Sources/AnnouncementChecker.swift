import Foundation


@MainActor
final class AnnouncementChecker: ObservableObject {
    private static nonisolated let feedURL =
        "https://github.com/mont127/MacNdCheese/discussions/categories/announcements.atom"
    private static nonisolated let lastShownKey = "MacNCheese.LastShownAnnouncementID"

    @Published var hasNewAnnouncement = false
    /// An emergency announcement: a post in the Announcements category whose title starts
    /// with "🚨", "[Emergency]" or "Emergency:". It opens by itself at every launch, seen or
    /// not, until the post is retitled or deleted; "Don't show again" is not offered.
    @Published var isEmergency = false
    /// The feed entry's id, which is what "seen" is remembered by.
    @Published var entryID: String = ""
    @Published var title: String = ""
    @Published var htmlContent: String = ""
    @Published var plainTextContent: String = ""
    @Published var url: String = ""
    @Published var publishedDate: String = ""

    func markShown(id: String) {
        UserDefaults.standard.set(id, forKey: Self.lastShownKey)
        hasNewAnnouncement = false
    }

    /// Close the announcement for this session without remembering it as seen
    /// (an emergency one comes back at the next launch).
    func dismissForNow() {
        hasNewAnnouncement = false
    }

    private static nonisolated let emergencyMarkers = ["🚨", "[emergency]", "emergency:"]

    nonisolated static func isEmergencyTitle(_ title: String) -> Bool {
        let t = title.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return emergencyMarkers.contains { t.hasPrefix($0) }
    }

    /// The title without its emergency marker(s); the sheet's header already says it.
    nonisolated static func strippingEmergencyMarkers(_ title: String) -> String {
        var t = title.trimmingCharacters(in: .whitespacesAndNewlines)
        var changed = true
        while changed {
            changed = false
            for m in emergencyMarkers where t.lowercased().hasPrefix(m) {
                t = String(t.dropFirst(m.count)).trimmingCharacters(in: .whitespacesAndNewlines)
                changed = true
            }
        }
        return t.isEmpty ? title : t
    }


    func check() {
        Task.detached(priority: .utility) {
            do {
                guard let url = URL(string: Self.feedURL) else { return }
                var request = URLRequest(url: url)
                request.setValue("MacNCheese-Announcements", forHTTPHeaderField: "User-Agent")
                request.timeoutInterval = 10

                let (data, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { return }
                guard let xml = String(data: data, encoding: .utf8) else { return }

                let entries = Self.parseEntries(xml)
                // The newest emergency post wins and is shown whether or not it was seen;
                // otherwise the newest post, once.
                let emergency = entries.first { Self.isEmergencyTitle($0.title) }
                guard let entry = emergency ?? entries.first else { return }
                if emergency == nil {
                    let lastShown = UserDefaults.standard.string(forKey: Self.lastShownKey) ?? ""
                    guard entry.id != lastShown else { return }
                }

                let plain = Self.htmlToPlain(entry.htmlContent)

                await MainActor.run {
                    self.isEmergency = emergency != nil
                    self.entryID = entry.id
                    self.title = emergency != nil ? Self.strippingEmergencyMarkers(entry.title) : entry.title
                    self.htmlContent = entry.htmlContent
                    self.plainTextContent = plain
                    self.url = entry.url
                    self.publishedDate = entry.published

                    self.hasNewAnnouncement = true
                }
            } catch {
               
            }
        }
    }

   

    nonisolated private struct Entry {
        let id: String
        let title: String
        let url: String
        let htmlContent: String
        let published: String
    }

    /// Every entry in the feed, newest first (the order GitHub writes them).
    nonisolated private static func parseEntries(_ xml: String) -> [Entry] {
        var out: [Entry] = []
        var rest = xml[...]
        while let lo = rest.range(of: "<entry>")?.upperBound,
              let hi = rest.range(of: "</entry>", range: lo..<rest.endIndex) {
            if let e = parseEntry(String(rest[lo..<hi.lowerBound])) { out.append(e) }
            rest = rest[hi.upperBound...]
        }
        return out
    }

    nonisolated private static func parseEntry(_ entryXml: String) -> Entry? {

        let title   = (extractTag(entryXml, "title") ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let id      = (extractTag(entryXml, "id") ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let pub     = (extractTag(entryXml, "published") ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let link    = extractLinkHref(entryXml) ?? ""
        let content = decodeEntities(extractTag(entryXml, "content") ?? "")

        guard !title.isEmpty, !link.isEmpty else { return nil }
        return Entry(id: id.isEmpty ? link : id,
                     title: title,
                     url: link,
                     htmlContent: content,
                     published: pub)
    }

    nonisolated private static func rangeBetween(_ s: String, start: String, end: String) -> Range<String.Index>? {
        guard let lo = s.range(of: start)?.upperBound else { return nil }
        guard let hi = s.range(of: end, range: lo..<s.endIndex)?.lowerBound else { return nil }
        return lo..<hi
    }

    nonisolated private static func extractTag(_ xml: String, _ tag: String) -> String? {
        
        let patterns = ["<\(tag)>", "<\(tag) "]
        for p in patterns {
            guard let openRange = xml.range(of: p) else { continue }
            // Find the closing '>' of the opening tag (handles attributes)
            guard let openEnd = xml.range(of: ">", range: openRange.lowerBound..<xml.endIndex)?.upperBound else { continue }
            guard let closeStart = xml.range(of: "</\(tag)>", range: openEnd..<xml.endIndex)?.lowerBound else { continue }
            return String(xml[openEnd..<closeStart])
        }
        return nil
    }

    nonisolated private static func extractLinkHref(_ xml: String) -> String? {
        // <link type="text/html" rel="alternate" href="https://..."/>
        guard let linkRange = xml.range(of: "<link") else { return nil }
        guard let linkEnd = xml.range(of: "/>", range: linkRange.lowerBound..<xml.endIndex)?.upperBound else { return nil }
        let linkTag = String(xml[linkRange.lowerBound..<linkEnd])

        guard let hrefStart = linkTag.range(of: "href=\"")?.upperBound else { return nil }
        guard let hrefEnd = linkTag.range(of: "\"", range: hrefStart..<linkTag.endIndex)?.lowerBound else { return nil }
        return String(linkTag[hrefStart..<hrefEnd])
    }

    
    nonisolated private static func decodeEntities(_ s: String) -> String {
        var r = s
        let replacements: [(String, String)] = [
            ("&lt;", "<"),
            ("&gt;", ">"),
            ("&quot;", "\""),
            ("&#39;", "'"),
            ("&apos;", "'"),
            ("&amp;", "&")  
        ]
        for (from, to) in replacements {
            r = r.replacingOccurrences(of: from, with: to)
        }
        return r.trimmingCharacters(in: .whitespacesAndNewlines)
    }


    nonisolated private static func htmlToPlain(_ html: String) -> String {
        var r = html
      
        r = r.replacingOccurrences(of: "<br>", with: "\n", options: .caseInsensitive)
        r = r.replacingOccurrences(of: "<br/>", with: "\n", options: .caseInsensitive)
        r = r.replacingOccurrences(of: "<br />", with: "\n", options: .caseInsensitive)
        r = r.replacingOccurrences(of: "</p>", with: "\n\n", options: .caseInsensitive)
        r = r.replacingOccurrences(of: "</li>", with: "\n", options: .caseInsensitive)
        r = r.replacingOccurrences(of: "<li>", with: "• ", options: .caseInsensitive)
   
        r = r.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
       
        r = r.replacingOccurrences(of: "\n{3,}", with: "\n\n", options: .regularExpression)
        return r.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
