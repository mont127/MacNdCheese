import Foundation


@MainActor
final class UpdateChecker: ObservableObject {
    static nonisolated let currentVersion = "7.1.0"
    private static nonisolated let githubRepo = "mont127/MacNdCheese"

    @Published var updateAvailable = false
    @Published var latestVersion = ""
    @Published var releaseURL = ""

    func check() {
        Task.detached(priority: .utility) {
            do {
                let apiURL = "https://api.github.com/repos/\(Self.githubRepo)/releases/latest"
                guard let url = URL(string: apiURL) else { return }

                var request = URLRequest(url: url)
                request.setValue("MacNCheese-Updater", forHTTPHeaderField: "User-Agent")
                request.timeoutInterval = 10

                let (data, response) = try await URLSession.shared.data(for: request)
                guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { return }
                guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }

                let tag = json["tag_name"] as? String ?? ""
                let htmlURL = json["html_url"] as? String
                    ?? "https://github.com/\(Self.githubRepo)/releases/latest"

                guard !tag.isEmpty else { return }

                let latestClean = tag.trimmingCharacters(in: CharacterSet(charactersIn: "v"))
                let currentClean = Self.currentVersion.trimmingCharacters(in: CharacterSet(charactersIn: "v"))

                if Self.compareVersions(latestClean, isNewerThan: currentClean) {
                    await MainActor.run {
                        self.latestVersion = tag
                        self.releaseURL = htmlURL
                        self.updateAvailable = true
                    }
                }
            } catch {
                
            }
        }
    }

   
    nonisolated private static func compareVersions(_ a: String, isNewerThan b: String) -> Bool {
        let aParts = a.split(separator: ".").compactMap { Int($0) }
        let bParts = b.split(separator: ".").compactMap { Int($0) }
        let count = max(aParts.count, bParts.count)
        for i in 0..<count {
            let av = i < aParts.count ? aParts[i] : 0
            let bv = i < bParts.count ? bParts[i] : 0
            if av > bv { return true }
            if av < bv { return false }
        }
        return false
    }
}
