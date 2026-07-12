import AppIntents
import AppKit
import Foundation

extension Notification.Name {
    static let launchGameFromIntent   = Notification.Name("MacNCheese.launchGameFromIntent")
    static let launchGameFromSpotlight = Notification.Name("MacNCheese.launchGameFromSpotlight")
}

/// Routes deep links and App Intent launches to BackendClient.
/// Handles URL scheme: macncheese://launch?bottle=<path>&game=<appid>
///                 and macncheese://bottle?path=<path>
@MainActor
final class MacNCheeseURLHandler {
    let backend: BackendClient

    init(backend: BackendClient) {
        self.backend = backend
    }

    // MARK: - URL scheme entry point (onOpenURL / Spotlight tap)

    func handle(_ url: URL) {
        guard url.scheme?.lowercased() == "macncheese",
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let host = components.host else { return }

        switch host {
        case "launch":
            guard let bottlePath = components.queryValue("bottle"),
                  let appid = components.queryValue("game") else { return }
            launch(bottlePath: bottlePath, appid: appid)
        case "bottle":
            guard let bottlePath = components.queryValue("path") else { return }
            backend.selectBottle(bottlePath)
        default:
            break
        }
    }

    // MARK: - Direct launch entry point (App Intent, running in-process)

    func launch(bottlePath: String, appid: String) {
        if backend.activePrefix != bottlePath {
            backend.selectBottle(bottlePath)
        }
        // Poll until scanGames populates games for this bottle, then launch.
        Task {
            for _ in 0..<20 {
                if let game = backend.games.first(where: { $0.appid == appid }) {
                    await performLaunch(game: game, bottlePath: bottlePath)
                    return
                }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
        }
    }

    // MARK: - Private

    private func performLaunch(game: Game, bottlePath: String) async {
        let cfg = await backend.getGameConfig(prefix: bottlePath, appid: game.appid)
        let msync = cfg["msync"] as? Bool ?? true
        let esync = msync ? false : (cfg["esync"] as? Bool ?? true)
        let backend_ = cfg["backend"] as? String ?? "auto"
        let retinaMode = cfg["retina_mode"] as? Bool ?? false
        let metalHud = cfg["metal_hud"] as? Bool ?? false
        let customEnv = cfg["custom_env"] as? String ?? ""
        let args = cfg["args"] as? String ?? ""

        if let epicAppName = game.epicAppName, !epicAppName.isEmpty {
            await backend.epicLaunchGame(
                prefix: bottlePath,
                appName: epicAppName,
                backend: backend_,
                retinaMode: retinaMode,
                metalHud: metalHud,
                esync: esync,
                msync: msync,
                customEnv: customEnv
            )
        } else {
            let cfgExe = cfg["exe"] as? String ?? ""
            let exe = cfgExe.isEmpty ? (game.exe ?? "") : cfgExe
            guard !exe.isEmpty else { return }
            await backend.launchGame(
                prefix: bottlePath,
                exe: exe,
                args: args,
                backend: backend_,
                installDir: game.installDir,
                retinaMode: retinaMode,
                metalHud: metalHud,
                esync: esync,
                msync: msync,
                gameName: game.name,
                steamAppId: game.appid,
                customEnv: customEnv
            )
        }

        // Donate the intent to the on-device prediction model each time a game
        // launches. IntentDonationManager feeds Apple Intelligence's LLM router:
        // after a few donations for the same game, the system learns to associate
        // natural language like "launch Among Us" with this specific intent+entity,
        // making Siri suggestions and invocation progressively more reliable.
        // AppIntents-based donation needs macOS 14+ (see GameEntity/LaunchGameIntent).
        if #available(macOS 14, *) {
            let cached = GameIndexCache.allGames()
                .first { $0.bottlePath == bottlePath && $0.appid == game.appid }
            if let cached {
                let intent = LaunchGameIntent()
                intent.game = GameEntity(
                    id: "\(cached.bottlePath):::\(cached.appid)",
                    name: cached.name,
                    bottlePath: cached.bottlePath,
                    bottleName: cached.bottleName,
                    appid: cached.appid
                )
                Task { try? await IntentDonationManager.shared.donate(intent: intent) }
            }
        }
    }
}

private extension URLComponents {
    func queryValue(_ name: String) -> String? {
        queryItems?.first { $0.name == name }?.value
    }
}
