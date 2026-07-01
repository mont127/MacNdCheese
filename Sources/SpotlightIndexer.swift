import CoreSpotlight
import Foundation

/// Indexes game entries into macOS Spotlight via CoreSpotlight.
///
/// UID strategy:
///   • Steam/manual games: "\(bottlePath):::\(appid)"  — per-bottle, because
///     Steam games are physically installed inside the bottle's prefix.
///   • Epic/Legendary games: "epic:::\(epicAppName)"   — global, because
///     Legendary's game library is shared across all bottles; every Epic bottle
///     scan returns the same installed games, so we need one deduped entry.
///   • Amazon/Nile games: "amazon:::\(amazonId)"        — global, same reasoning.
enum SpotlightIndexer {
    static let steamDomainPrefix = "com.marcel.macncheese.steam."
    static let epicDomain        = "com.marcel.macncheese.epic"
    static let amazonDomain      = "com.marcel.macncheese.amazon"

    static func domainForBottle(_ bottlePath: String) -> String {
        let safe = bottlePath
            .addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? bottlePath
        return "\(steamDomainPrefix)\(safe)"
    }

    /// Re-index all games for a bottle.
    /// Steam entries: delete old domain then re-add (removes uninstalled games).
    /// Epic entries: upsert only — same UID across bottles deduplicates automatically.
    static func index(games: [Game], bottlePath: String, bottleName: String) {
        let bottleDomain = domainForBottle(bottlePath)
        var steamItems: [CSSearchableItem] = []
        var epicItems:  [CSSearchableItem] = []
        var amazonItems: [CSSearchableItem] = []

        for game in games {
            let attrs = CSSearchableItemAttributeSet(contentType: .item)
            attrs.title = game.name
            attrs.contentDescription = "Windows game · \(bottleName)"
            attrs.keywords = [game.name, bottleName, "wine", "windows", "game", "macncheese"]

            if let epicAppName = game.epicAppName, !epicAppName.isEmpty {
                epicItems.append(CSSearchableItem(
                    uniqueIdentifier: "epic:::\(epicAppName)",
                    domainIdentifier: epicDomain,
                    attributeSet: attrs
                ))
            } else if let amazonId = game.amazonId, !amazonId.isEmpty {
                amazonItems.append(CSSearchableItem(
                    uniqueIdentifier: "amazon:::\(amazonId)",
                    domainIdentifier: amazonDomain,
                    attributeSet: attrs
                ))
            } else {
                steamItems.append(CSSearchableItem(
                    uniqueIdentifier: "\(bottlePath):::\(game.appid)",
                    domainIdentifier: bottleDomain,
                    attributeSet: attrs
                ))
            }
        }

        // Steam: wipe old domain first (handles uninstalls), then re-index.
        CSSearchableIndex.default().deleteSearchableItems(withDomainIdentifiers: [bottleDomain]) { _ in
            if !steamItems.isEmpty {
                CSSearchableIndex.default().indexSearchableItems(steamItems) { error in
                    if let error { print("[Spotlight] Steam index error: \(error.localizedDescription)") }
                }
            }
        }

        // Epic: upsert — CoreSpotlight deduplicates by UID, so all bottles sharing
        // the same game will converge on a single Spotlight entry.
        if !epicItems.isEmpty {
            CSSearchableIndex.default().indexSearchableItems(epicItems) { error in
                if let error { print("[Spotlight] Epic index error: \(error.localizedDescription)") }
            }
        }

        // Amazon: same upsert reasoning as Epic.
        if !amazonItems.isEmpty {
            CSSearchableIndex.default().indexSearchableItems(amazonItems) { error in
                if let error { print("[Spotlight] Amazon index error: \(error.localizedDescription)") }
            }
        }
    }

    /// Remove Spotlight entries when a bottle is deleted.
    static func deleteForBottle(_ bottlePath: String) {
        CSSearchableIndex.default().deleteSearchableItems(
            withDomainIdentifiers: [domainForBottle(bottlePath)]
        ) { _ in }
        // Epic/Amazon entries are global and shared — don't delete them when one bottle is removed.
    }

    static func deleteAll() {
        CSSearchableIndex.default().deleteAllSearchableItems { _ in }
    }
}
