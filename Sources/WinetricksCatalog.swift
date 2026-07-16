import Foundation

/// One installable winetricks component (a "verb"), as reported live by the
/// backend's `winetricks_catalog` command — parsed straight from the bundled
/// winetricks script's own `w_metadata` declarations, so this list is always
/// the complete, authoritative set the binary can actually install (not a
/// hand-picked subset that can drift out of date).
struct WinetricksVerb: Identifiable, Codable, Hashable {
    let id: String
    let category: String
    let title: String
    let publisher: String
    let year: String

    enum CodingKeys: String, CodingKey {
        case id, category, title, publisher, year
    }
}

struct WinetricksCategoryInfo {
    let name: String
    let systemImage: String
}

/// Presentation-only metadata for winetricks' own category taxonomy. Unlike
/// the verb list itself, this is legitimately static: there are exactly 5
/// categories in the bundled script (confirmed via `w_metadata` scan —
/// apps/benchmarks/dlls/fonts/settings; no "games" category exists despite
/// older winetricks docs mentioning one), and that taxonomy is part of
/// winetricks' own stable interface, not something MacNdCheese curates.
enum WinetricksCatalog {
    static let categoryInfo: [String: WinetricksCategoryInfo] = [
        "apps":       WinetricksCategoryInfo(name: L("Apps"), systemImage: "app.badge"),
        "dlls":       WinetricksCategoryInfo(name: L("DLLs & Libraries"), systemImage: "puzzlepiece"),
        "fonts":      WinetricksCategoryInfo(name: L("Fonts"), systemImage: "textformat"),
        "settings":   WinetricksCategoryInfo(name: L("Settings"), systemImage: "gearshape"),
        "benchmarks": WinetricksCategoryInfo(name: L("Benchmarks"), systemImage: "gauge"),
    ]

    static let categoryOrder = ["apps", "dlls", "fonts", "settings", "benchmarks"]

    static func info(for category: String) -> WinetricksCategoryInfo {
        categoryInfo[category] ?? WinetricksCategoryInfo(name: category.capitalized, systemImage: "shippingbox")
    }
}
