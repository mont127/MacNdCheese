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

/// A few winetricks verbs stomp on the exact DLLs MacNdCheese wires up itself, so
/// installin one into a working bottle (the Steam bottle espesially) silently undoes
/// that setup. The symptom shows up later as "Steam went black" / "my game stopped
/// renderin", with nothing obviously connectin it back to here.
///
/// We WARN rather than block: on a scratch bottle these r legitimate things to want,
/// and winetricks isnt the thing thats wrong -- the overlap with our own graphics
/// wiring is. Both families below r read out of the bundled winetricks script itself,
/// not guessd:
///   - `mf`  -> `w_override_dlls native,builtin mf`
///   - dxvk* / galliumnine* / vkd3d -> `helper_dxvk ... "dxgi,d3d8,d3d9,d3d10core,d3d11"`
/// which is exactly the d3d11/dxgi/d3d10core set the Steam (DXMT) n game (D3DMetal)
/// paths depend on. Prefix-matchd on purpose: winetricks ships ~50 pinned dxvk<version>
/// verbs n ~10 galliumnine ones n they all do the same thing.
enum WinetricksRisk {
    static func warning(for verb: String) -> String? {
        let v = verb.lowercased()
        if v == "mf" || v == "mfplat" {
            return L("This replaces the Media Foundation DLLs, which is known to stop Steam's web helper from drawing — Steam opens as a black window afterwards.")
        }
        if v.hasPrefix("dxvk") || v.hasPrefix("galliumnine") || v.hasPrefix("vkd3d") {
            return L("This overwrites d3d11, dxgi and d3d10core in this bottle — the same files MacNdCheese sets up for Steam and for game rendering. Steam or your games may stop rendering until the bottle is set up again.")
        }
        return nil
    }
}
