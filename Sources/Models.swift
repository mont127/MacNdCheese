import Foundation

struct Bottle: Identifiable, Codable, Hashable {
    var id: String { path }
    let path: String
    var name: String
    var iconPath: String?
    var launcherExe: String?
    var launcherType: String?   // "steam", "custom"; nil treated as "steam" for compat
    var defaultBackend: String? // "auto", "dxvk", etc.

    /// True when this bottle uses Steam as its launcher.
    var isSteamBottle: Bool {
        guard let lt = launcherType else { return true }
        return lt == "steam"
    }

    enum CodingKeys: String, CodingKey {
        case path, name
        case iconPath = "icon_path"
        case launcherExe = "launcher_exe"
        case launcherType = "launcher_type"
        case defaultBackend = "default_backend"
    }
}

struct Game: Identifiable, Codable, Hashable {
    var id: String { appid }
    let appid: String
    let name: String
    let exe: String?
    let installDir: String
    let coverUrl: String?
    let isManual: Bool

    enum CodingKeys: String, CodingKey {
        case appid, name, exe
        case installDir = "install_dir"
        case coverUrl = "cover_url"
        case isManual = "is_manual"
    }
}

struct BackendStatus: Codable {
    let wineFound: Bool
    let winePath: String?
    let hasDxvk: Bool
    let hasMesa: Bool

    enum CodingKeys: String, CodingKey {
        case wineFound = "wine_found"
        case winePath = "wine_path"
        case hasDxvk = "has_dxvk"
        case hasMesa = "has_mesa"
    }
}

struct LaunchResult: Codable {
    let pid: Int
    let logPath: String
    let backend: String?

    enum CodingKeys: String, CodingKey {
        case pid
        case logPath = "log_path"
        case backend
    }
}

struct GraphicsBackend: Identifiable, Codable {
    var id: String { backendId }
    let backendId: String
    let label: String
    let available: Bool

    enum CodingKeys: String, CodingKey {
        case backendId = "id"
        case label
        case available
    }
}

struct BackendsResponse: Codable {
    let backends: [GraphicsBackend]
    let autoResolved: String

    enum CodingKeys: String, CodingKey {
        case backends
        case autoResolved = "auto_resolved"
    }
}

struct ComponentsStatus: Codable {
    let hasTools: Bool
    let hasWine: Bool
    let hasMesa: Bool
    let hasDxvk64: Bool
    let hasDxvk32: Bool
    let hasGptkFull: Bool
    let hasD3dMetal3: Bool
    let hasGptk: Bool

    enum CodingKeys: String, CodingKey {
        case hasTools = "has_tools"
        case hasWine = "has_wine"
        case hasMesa = "has_mesa"
        case hasDxvk64 = "has_dxvk64"
        case hasDxvk32 = "has_dxvk32"
        case hasGptkFull = "has_gptk_full"
        case hasD3dMetal3 = "has_d3dmetal3"
        case hasGptk = "has_gptk"
    }
}
