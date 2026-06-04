import Foundation
import AppKit


@MainActor
final class UpdateChecker: ObservableObject {
    /// Read from the running bundle so it never goes stale (was hardcoded).
    static nonisolated var currentVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"
    }
    private static nonisolated let githubRepo = "mont127/MacNdCheese"

    @Published var updateAvailable = false
    @Published var latestVersion = ""
    @Published var releaseURL = ""
    @Published var dmgURL = ""

    // In-app updater state
    @Published var installing = false
    @Published var installDone = false
    @Published var installFailed = false
    @Published var currentStep = ""
    @Published var installLog: [String] = []

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

                // Locate the .dmg asset so the in-app updater can download it.
                let assets = json["assets"] as? [[String: Any]] ?? []
                let dmgAsset = assets.first { ($0["name"] as? String ?? "").lowercased().hasSuffix(".dmg") }
                let dmgDownload = dmgAsset?["browser_download_url"] as? String ?? ""

                let latestClean = tag.trimmingCharacters(in: CharacterSet(charactersIn: "vV"))
                let currentClean = Self.currentVersion.trimmingCharacters(in: CharacterSet(charactersIn: "vV"))

                if Self.compareVersions(latestClean, isNewerThan: currentClean) {
                    await MainActor.run {
                        self.latestVersion = tag
                        self.releaseURL = htmlURL
                        self.dmgURL = dmgDownload
                        self.updateAvailable = true
                    }
                }
            } catch {

            }
        }
    }

    /// Download the newest DMG, extract+codesign the .app, then quit so the
    /// detached backend swapper replaces this app and relaunches it.
    func install(backend: BackendClient) {
        guard !installing, !dmgURL.isEmpty else { return }
        installing = true
        installFailed = false
        installDone = false
        installLog = []
        currentStep = L("Starting…")
        let appPath = Bundle.main.bundlePath
        let pid = Int(ProcessInfo.processInfo.processIdentifier)
        let dmg = dmgURL
        Task {
            guard let jobId = await backend.applyAppUpdate(appPath: appPath, appPid: pid, dmgURL: dmg) else {
                self.installFailed = true
                self.installing = false
                self.currentStep = L("Couldn't start update")
                return
            }
            var offset = 0
            while true {
                try? await Task.sleep(nanoseconds: 600_000_000)
                guard let p = await backend.getInstallProgress(jobId: jobId, offset: offset) else { break }
                self.installLog.append(contentsOf: p.lines)
                offset = p.totalLines
                if !p.current.isEmpty { self.currentStep = p.current }
                if p.done {
                    if p.failed {
                        self.installFailed = true
                        self.installing = false
                        self.currentStep = L("Update failed")
                    } else {
                        self.installDone = true
                        self.currentStep = L("Restarting…")
                        // Swapper is detached and waiting for this process to exit.
                        try? await Task.sleep(nanoseconds: 600_000_000)
                        NSApplication.shared.terminate(nil)
                    }
                    break
                }
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
