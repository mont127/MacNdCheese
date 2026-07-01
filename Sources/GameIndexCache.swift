import Foundation

/// A slim, Codable representation of a game stored in UserDefaults so that
/// AppIntent EntityQuery (non-isolated) can read game/bottle data without
/// going through the @MainActor BackendClient.
struct CachedGame: Codable {
    var appid: String
    var name: String
    var bottlePath: String
    var bottleName: String
    var coverUrl: String?
    var exe: String?
    var epicAppName: String?
    var amazonId: String?
    var installDir: String
}

struct CachedBottle: Codable {
    var path: String
    var name: String
    var launcherType: String?
}

/// Thread-safe, nonisolated cache backed by UserDefaults.standard.
/// BackendClient writes to it on @MainActor after each scanGames / loadBottles.
/// AppIntent EntityQuery reads from it on any thread without hitting @MainActor.
///
/// NOTE: If an IntentExtension target is ever added, migrate to
/// UserDefaults(suiteName: "group.com.marcel.macncheese") + App Group entitlement.
enum GameIndexCache {
    private static let gamesKey = "GameIndexCache.games.v1"
    private static let bottlesKey = "GameIndexCache.bottles.v1"
    private static let encoder = JSONEncoder()
    private static let decoder = JSONDecoder()

    /// Replaces cached games for `bottlePath` with the newly scanned list.
    static func updateGames(_ games: [Game], bottlePath: String, bottleName: String) {
        var cached = allGames().filter { $0.bottlePath != bottlePath }
        let fresh = games.map { g in
            CachedGame(
                appid: g.appid,
                name: g.name,
                bottlePath: bottlePath,
                bottleName: bottleName,
                coverUrl: g.coverUrl,
                exe: g.exe,
                epicAppName: g.epicAppName,
                amazonId: g.amazonId,
                installDir: g.installDir
            )
        }
        cached.append(contentsOf: fresh)
        save(cached, forKey: gamesKey)
    }

    static func updateBottles(_ bottles: [Bottle]) {
        let cached = bottles.map { CachedBottle(path: $0.path, name: $0.name, launcherType: $0.launcherType) }
        save(cached, forKey: bottlesKey)
    }

    static func allGames() -> [CachedGame] {
        guard let data = UserDefaults.standard.data(forKey: gamesKey) else { return [] }
        return (try? decoder.decode([CachedGame].self, from: data)) ?? []
    }

    static func allBottles() -> [CachedBottle] {
        guard let data = UserDefaults.standard.data(forKey: bottlesKey) else { return [] }
        return (try? decoder.decode([CachedBottle].self, from: data)) ?? []
    }

    /// Look up a game by its Spotlight uniqueIdentifier.
    /// Epic games use "epic:::<epicAppName>"; Amazon games use "amazon:::<amazonId>";
    /// Steam/manual use "\(bottlePath):::\(appid)".
    static func game(byUID uid: String) -> CachedGame? {
        let parts = uid.components(separatedBy: ":::")
        guard parts.count >= 2 else { return nil }

        if parts[0] == "epic" {
            let epicAppName = parts[1]
            return allGames().first { $0.epicAppName == epicAppName }
        } else if parts[0] == "amazon" {
            let amazonId = parts[1]
            return allGames().first { $0.amazonId == amazonId }
        } else {
            let appid = parts.last!
            let bottlePath = parts.dropLast().joined(separator: ":::")
            return allGames().first { $0.bottlePath == bottlePath && $0.appid == appid }
        }
    }

    static func removeGames(forBottle bottlePath: String) {
        let filtered = allGames().filter { $0.bottlePath != bottlePath }
        save(filtered, forKey: gamesKey)
    }

    private static func save<T: Encodable>(_ value: T, forKey key: String) {
        if let data = try? encoder.encode(value) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }
}
