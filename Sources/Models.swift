import Foundation

struct Bottle: Identifiable, Codable, Hashable {
    var id: String { path }
    let path: String
    var name: String
    var iconPath: String?
    var launcherExe: String?
    var launcherType: String?   // "steam", "custom", "epic"; nil treated as "steam" for compat
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

    /// Cheap, synchronous, client-side check — the SwiftUI app and the Python
    /// backend both run on the same Mac, so no backend round-trip is needed
    /// to know whether this bottle's folder (e.g. on an external drive) is
    /// currently present. Mirrors the FileManager check already used in
    /// SidebarView.BottleRow.loadIcon().
    var isReachable: Bool {
        FileManager.default.fileExists(atPath: path)
    }

    /// Path to the bottle's own launcher exe — the custom `launcherExe` if
    /// set, else the default Steam.exe location for Steam bottles. nil for a
    /// bare manual bottle with no specific launcher (its empty-state buttons
    /// are generic file pickers, not tied to a fixed exe, so there's nothing
    /// meaningful to check).
    var launcherExePath: String? {
        if let exe = launcherExe, !exe.isEmpty { return exe }
        if isSteamBottle { return path + "/drive_c/Program Files (x86)/Steam/Steam.exe" }
        return nil
    }

    /// True when there's no specific launcher to check, or when there is and
    /// it's currently present on disk. Distinct from `isReachable`: the
    /// bottle folder can be on the internal drive (reachable) while the
    /// launcher exe itself sits behind a symlink into a now-disconnected
    /// external drive.
    var isLauncherReachable: Bool {
        guard let exe = launcherExePath else { return true }
        return FileManager.default.fileExists(atPath: exe)
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
    var updateAvailable: Bool
    var epicAppName: String?

    enum CodingKeys: String, CodingKey {
        case appid, name, exe
        case installDir = "install_dir"
        case coverUrl = "cover_url"
        case isManual = "is_manual"
        case isInstalled = "is_installed"
        case updateAvailable = "update_available"
        case epicAppName = "epic_app_name"
    }

    // Tolerant decoder: older backends omit is_installed / update_available /
    // epic_app_name. Treat absent install state as installed, the rest as off.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        appid = try c.decode(String.self, forKey: .appid)
        name = try c.decode(String.self, forKey: .name)
        exe = try c.decodeIfPresent(String.self, forKey: .exe)
        installDir = try c.decodeIfPresent(String.self, forKey: .installDir) ?? ""
        coverUrl = try c.decodeIfPresent(String.self, forKey: .coverUrl)
        isManual = try c.decodeIfPresent(Bool.self, forKey: .isManual) ?? false
        isInstalled = try c.decodeIfPresent(Bool.self, forKey: .isInstalled) ?? true
        updateAvailable = try c.decodeIfPresent(Bool.self, forKey: .updateAvailable) ?? false
        epicAppName = try c.decodeIfPresent(String.self, forKey: .epicAppName)
    }

    /// True unless this game's install folder is a real, known path that's
    /// currently missing on disk — e.g. installDir is a symlink into a game
    /// that was moved to (or lives entirely on) a now-disconnected external
    /// drive, even though the bottle itself is on the internal drive and
    /// otherwise reachable. The backend's own is_installed/exe detection
    /// can't tell "never installed" apart from "currently unreachable" for
    /// this case, so this is a client-side check, same idea as
    /// Bottle.isReachable. Epic games may legitimately have no installDir
    /// yet (not downloaded) — that's not a missing-drive case, so it's
    /// excluded here.
    var isReachable: Bool {
        guard epicAppName == nil, !installDir.isEmpty else { return true }
        return FileManager.default.fileExists(atPath: installDir)
    }
}

/// A Windows application discovered inside a bottle (Start Menu shortcut or
/// Program Files executable). Distinct from `Game` so it never disturbs the
/// games grid or ordering logic.
struct WineApp: Identifiable, Codable, Hashable {
    var id: String { exe }
    let name: String
    let exe: String
    var args: String
    var iconBase64: String?

    enum CodingKeys: String, CodingKey {
        case name, exe, args
        case iconBase64 = "icon"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        exe = try c.decode(String.self, forKey: .exe)
        args = try c.decodeIfPresent(String.self, forKey: .args) ?? ""
        iconBase64 = try c.decodeIfPresent(String.self, forKey: .iconBase64)
    }

    /// Same idea as Game.isReachable / Bottle.isReachable — catches a Start
    /// Menu / Program Files app whose exe sits behind a symlink into a
    /// now-disconnected external drive.
    var isReachable: Bool {
        FileManager.default.fileExists(atPath: exe)
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

// Bradar this struct is holding the info about the microfone bradar the name the rate the warning everything
struct AudioInpitInfo: Codable {
    // Bradar what is this comment delet this
    let name: String
    let rate: Int
    let transport: String
    let warn: Bool
    let message: String
    let suggest: String
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
    let hasWineDevel: Bool
    let hasMesa: Bool
    let hasDxvk64: Bool
    let hasDxvk32: Bool
    let hasDxmt: Bool
    let hasDxmtOpenXR: Bool
    let hasGptkDlls: Bool
    let hasD3dMetal3: Bool
    let hasWineD3DMetal: Bool
    let hasWineUnified: Bool
    let hasVkd3d: Bool
    let hasWineOpenXR: Bool
    let hasMonadoRuntime: Bool
    let wineVersion: String?

    enum CodingKeys: String, CodingKey {
        case hasTools = "has_tools"
        case hasWine = "has_wine"
        case hasWineStable = "has_wine_stable"
        case hasWineStaging = "has_wine_staging"
        case hasWineDevel = "has_wine_devel"
        case hasMesa = "has_mesa"
        case hasDxvk64 = "has_dxvk64"
        case hasDxvk32 = "has_dxvk32"
        case hasDxmt = "has_dxmt"
        case hasDxmtOpenXR = "has_dxmt_openxr"
        case hasGptkDlls = "has_gptk_dlls"
        case hasD3dMetal3 = "has_d3dmetal3"
        case hasWineD3DMetal = "has_wine_d3dmetal"
        case hasWineUnified = "has_wine_unified"
        case hasVkd3d = "has_vkd3d"
        case hasWineOpenXR = "has_wineopenxr"
        case hasMonadoRuntime = "has_monado_runtime"
        case wineVersion = "wine_version"
    }

    // Backwards-compat init for older backends that don't yet send
    // has_wine_devel / has_wineopenxr. Treat as absent → false.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hasTools          = try c.decode(Bool.self, forKey: .hasTools)
        hasWine           = try c.decode(Bool.self, forKey: .hasWine)
        hasWineStable     = try c.decode(Bool.self, forKey: .hasWineStable)
        hasWineStaging    = try c.decode(Bool.self, forKey: .hasWineStaging)
        hasWineDevel      = try c.decodeIfPresent(Bool.self, forKey: .hasWineDevel) ?? false
        hasMesa           = try c.decode(Bool.self, forKey: .hasMesa)
        hasDxvk64         = try c.decode(Bool.self, forKey: .hasDxvk64)
        hasDxvk32         = try c.decode(Bool.self, forKey: .hasDxvk32)
        hasDxmt           = try c.decode(Bool.self, forKey: .hasDxmt)
        hasDxmtOpenXR     = try c.decodeIfPresent(Bool.self, forKey: .hasDxmtOpenXR) ?? false
        hasGptkDlls       = try c.decode(Bool.self, forKey: .hasGptkDlls)
        hasD3dMetal3      = try c.decode(Bool.self, forKey: .hasD3dMetal3)
        hasWineD3DMetal   = try c.decodeIfPresent(Bool.self, forKey: .hasWineD3DMetal) ?? false
        hasWineUnified    = try c.decodeIfPresent(Bool.self, forKey: .hasWineUnified) ?? false
        hasVkd3d          = try c.decode(Bool.self, forKey: .hasVkd3d)
        hasWineOpenXR     = try c.decodeIfPresent(Bool.self, forKey: .hasWineOpenXR) ?? false
        hasMonadoRuntime  = try c.decodeIfPresent(Bool.self, forKey: .hasMonadoRuntime) ?? false
        wineVersion       = try c.decodeIfPresent(String.self, forKey: .wineVersion)
    }
}

/// Steam store media for a game: description + showcase screenshots, fetched
/// from the Steam appdetails endpoint by the backend `get_steam_media` command.
struct SteamMedia: Codable {
    let appid: String
    let description: String
    let shortDescription: String
    let headerImage: String
    let screenshots: [String]   // full-size URLs
    let thumbnails: [String]    // thumbnail URLs (parallel to screenshots)

    enum CodingKeys: String, CodingKey {
        case appid, description, screenshots, thumbnails
        case shortDescription = "short_description"
        case headerImage = "header_image"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        appid = try c.decodeIfPresent(String.self, forKey: .appid) ?? ""
        description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
        shortDescription = try c.decodeIfPresent(String.self, forKey: .shortDescription) ?? ""
        headerImage = try c.decodeIfPresent(String.self, forKey: .headerImage) ?? ""
        screenshots = try c.decodeIfPresent([String].self, forKey: .screenshots) ?? []
        thumbnails = try c.decodeIfPresent([String].self, forKey: .thumbnails) ?? []
    }
}

/// One installed (or missing) Wine build on disk, as probed live by the backend
/// `detect_wine` command. Powers the Bottle tab's detection-driven Wine selector.
struct WineVariant: Identifiable, Codable, Hashable {
    let id: String          // "stable" | "staging" | "devel" | "d3dmetal"
    let label: String
    let selectable: Bool    // true for the variants wine_binary actually honours
    let installed: Bool
    let path: String
    let version: String?

    enum CodingKeys: String, CodingKey {
        case id, label, selectable, installed, path, version
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        label = try c.decode(String.self, forKey: .label)
        selectable = try c.decodeIfPresent(Bool.self, forKey: .selectable) ?? true
        installed = try c.decodeIfPresent(Bool.self, forKey: .installed) ?? false
        path = try c.decodeIfPresent(String.self, forKey: .path) ?? ""
        version = try c.decodeIfPresent(String.self, forKey: .version)
    }
}

struct WineDetection: Codable {
    let variants: [WineVariant]
    let autoResolvedId: String?
    let autoResolvedPath: String?
    let autoResolvedVersion: String?

    enum CodingKeys: String, CodingKey {
        case variants
        case autoResolvedId = "auto_resolved_id"
        case autoResolvedPath = "auto_resolved_path"
        case autoResolvedVersion = "auto_resolved_version"
    }

    /// Look up one detected variant by its id ("stable", "staging", …).
    func variant(_ id: String) -> WineVariant? { variants.first { $0.id == id } }
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

struct CheeseDiagnosis: Codable {
    let generatedAt: String
    let prefix: String
    let summary: String
    let checks: [CheeseDiagnosticCheck]
    let repairs: [CheeseRepairAction]

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case prefix, summary, checks, repairs
    }
}

struct CheeseDiagnosticCheck: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let status: String
    let message: String
    let details: String
    let repairActions: [String]

    enum CodingKeys: String, CodingKey {
        case id, title, status, message, details
        case repairActions = "repair_actions"
    }
}

struct CheeseRepairAction: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let details: String
    let destructive: Bool
    let recommended: Bool
}

/// Transient per-game Epic (Legendary) download/queue state. UI-only, not Codable.
struct EpicDownloadState {
    var progress: Double
    var queued: Bool
    var queuePosition: Int
    var paused: Bool = false   // from the PR; default keeps existing call sites valid
    var prefix: String
}
