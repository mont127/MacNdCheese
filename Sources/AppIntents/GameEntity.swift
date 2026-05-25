import AppIntents

/// An AppEntity representing an installed game, resolved from GameIndexCache
/// so Siri and Shortcuts can offer game names as typed parameters.
struct GameEntity: AppEntity {
    static let typeDisplayRepresentation = TypeDisplayRepresentation(name: "Game")
    static let defaultQuery = GameEntityQuery()

    /// Composite unique ID: "\(bottlePath):::\(appid)" — stable across app launches.
    var id: String
    var name: String
    var bottlePath: String
    var bottleName: String
    var appid: String

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(name)", subtitle: "in \(bottleName)")
    }
}

struct GameEntityQuery: EntityQuery, EntityStringQuery {
    func entities(for identifiers: [String]) async throws -> [GameEntity] {
        let all = GameIndexCache.allGames()
        return identifiers.compactMap { id in
            guard let cached = all.first(where: { "\($0.bottlePath):::\($0.appid)" == id }) else {
                return nil
            }
            return GameEntity(
                id: id,
                name: cached.name,
                bottlePath: cached.bottlePath,
                bottleName: cached.bottleName,
                appid: cached.appid
            )
        }
    }

    func suggestedEntities() async throws -> [GameEntity] {
        GameIndexCache.allGames().map { cached in
            GameEntity(
                id: "\(cached.bottlePath):::\(cached.appid)",
                name: cached.name,
                bottlePath: cached.bottlePath,
                bottleName: cached.bottleName,
                appid: cached.appid
            )
        }
    }

    /// Enables Siri to fuzzy-match a spoken game name to a GameEntity.
    func entities(matching string: String) async throws -> [GameEntity] {
        let lower = string.lowercased()
        return GameIndexCache.allGames()
            .filter { $0.name.lowercased().contains(lower) }
            .map { cached in
                GameEntity(
                    id: "\(cached.bottlePath):::\(cached.appid)",
                    name: cached.name,
                    bottlePath: cached.bottlePath,
                    bottleName: cached.bottleName,
                    appid: cached.appid
                )
            }
    }
}
