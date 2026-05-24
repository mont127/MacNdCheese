import Foundation

struct Bottle: Identifiable, Codable, Hashable {
    var id: String { path }
    let path: String
    var name: String
    var iconPath: String?
    var launcherExe: String?
    var launcherType: String?   // "steam", "custom"; nil treated as "steam" for compat
    var defaultBackend: String? // "auto", "dxvk", etc.
    var wineBinary: String?     // "auto", "stable", "staging"

    /// True when this bottle uses Steam as its launcher.
    var isSteamBottle: Bool {
        guard let lt = launcherType else { return true }
        return lt == "steam"
    }

    /// True when this bottle uses Epic Games (Legendary) as its launcher.
    var isEpicBottle: Bool { launcherType == "epic" }

    enum CodingKeys: String, CodingKey {
        case path, name
        case iconPath = "icon_path"
        case launcherExe = "launcher_exe"
        case launcherType = "launcher_type"
        case defaultBackend = "default_backend"
        case wineBinary = "wine_binary"
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
    var isInstalled: Bool
    var epicAppName: String?

    enum CodingKeys: String, CodingKey {
        case appid, name, exe
        case installDir = "install_dir"
        case coverUrl = "cover_url"
        case isManual = "is_manual"
        case isInstalled = "is_installed"
        case epicAppName = "epic_app_name"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        appid = try c.decode(String.self, forKey: .appid)
        name = try c.decode(String.self, forKey: .name)
        exe = try c.decodeIfPresent(String.self, forKey: .exe)
        installDir = try c.decodeIfPresent(String.self, forKey: .installDir) ?? ""
        coverUrl = try c.decodeIfPresent(String.self, forKey: .coverUrl)
        isManual = try c.decodeIfPresent(Bool.self, forKey: .isManual) ?? false
        isInstalled = try c.decodeIfPresent(Bool.self, forKey: .isInstalled) ?? true
        epicAppName = try c.decodeIfPresent(String.self, forKey: .epicAppName)
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
    let hasWineStable: Bool
    let hasWineStaging: Bool
    let hasMesa: Bool
    let hasDxvk64: Bool
    let hasDxvk32: Bool
    let hasDxmt: Bool
    let hasGptkDlls: Bool
    let hasD3dMetal3: Bool
    let hasVkd3d: Bool
    let hasWineD3DMetal: Bool
    let hasWineOpenXR: Bool
    let wineVersion: String?

    enum CodingKeys: String, CodingKey {
        case hasTools = "has_tools"
        case hasWine = "has_wine"
        case hasWineStable = "has_wine_stable"
        case hasWineStaging = "has_wine_staging"
        case hasMesa = "has_mesa"
        case hasDxvk64 = "has_dxvk64"
        case hasDxvk32 = "has_dxvk32"
        case hasDxmt = "has_dxmt"
        case hasGptkDlls = "has_gptk_dlls"
        case hasD3dMetal3 = "has_d3dmetal3"
        case hasVkd3d = "has_vkd3d"
        case hasWineD3DMetal = "has_wine_d3dmetal"
        case hasWineOpenXR = "has_wineopenxr"
        case wineVersion = "wine_version"
    }
}

struct InstallProgress: Codable {
    let lines: [String]
    let totalLines: Int
    let done: Bool
    let failed: Bool
    let current: String

    enum CodingKeys: String, CodingKey {
        case lines
        case totalLines = "total_lines"
        case done
        case failed
        case current
    }
}

struct CheeseDiagnosis: Codable {
    let summary: String
    let generatedAt: String
    let checks: [CheeseDiagnosticCheck]
    let repairs: [CheeseRepairAction]

    enum CodingKeys: String, CodingKey {
        case summary
        case generatedAt = "generated_at"
        case checks
        case repairs
    }
}

struct CheeseDiagnosticCheck: Identifiable, Codable {
    let id: String
    let status: String
    let title: String
    let message: String
    let details: String
}

struct CheeseRepairAction: Identifiable, Codable {
    let id: String
    let title: String
    let details: String
    let recommended: Bool
    let destructive: Bool
}

struct UpdateInfo: Codable {
    let cheeseLatestTag: String?
    let gcenxLatestTag: String?
    let gcenxLatestName: String?
    let dxmtLatestTag: String?
    let dxmtLatestName: String?
    let toolsUpdateAvailable: Bool
    let wineUpdateAvailable: Bool
    let wineStableUpdateAvailable: Bool
    let wineStagingUpdateAvailable: Bool
    let dxmtUpdateAvailable: Bool

    enum CodingKeys: String, CodingKey {
        case cheeseLatestTag = "cheese_latest_tag"
        case gcenxLatestTag = "gcenx_latest_tag"
        case gcenxLatestName = "gcenx_latest_name"
        case dxmtLatestTag = "dxmt_latest_tag"
        case dxmtLatestName = "dxmt_latest_name"
        case toolsUpdateAvailable = "tools_update_available"
        case wineUpdateAvailable = "wine_update_available"
        case wineStableUpdateAvailable = "wine_stable_update_available"
        case wineStagingUpdateAvailable = "wine_staging_update_available"
        case dxmtUpdateAvailable = "dxmt_update_available"
    }
}
