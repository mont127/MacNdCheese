import SwiftUI
import Foundation

/// Supported UI languages. English is the SOURCE language — the keys passed to
/// `L(...)` are the exact English strings — and Chinese is looked up in
/// `Localization.zh`. Adding a language = add a case + a dictionary.
enum AppLanguage: String, CaseIterable, Identifiable {
    case en
    case zh

    var id: String { rawValue }

    /// Shown natively in the picker so each option is readable in its own script.
    var displayName: String {
        switch self {
        case .en: return "English"
        case .zh: return "中文"
        }
    }

    var englishName: String {
        switch self {
        case .en: return "English"
        case .zh: return "Chinese"
        }
    }
}

/// Runtime localization for the whole app. Every user-facing string is read
/// through `L("English text")`, which returns the English source as-is, or its
/// Chinese translation when `language == .zh`. Changing `language` republishes;
/// combined with `.id(loc.language)` on the root content the entire UI
/// re-renders live, with no app restart.
final class LocalizationManager: ObservableObject {
    static let shared = LocalizationManager()

    private static let storageKey = "app_language"

    @Published var language: AppLanguage {
        didSet {
            guard oldValue != language else { return }
            UserDefaults.standard.set(language.rawValue, forKey: Self.storageKey)
        }
    }

    /// True until the user has picked a language. Drives the first-launch popup.
    @Published var needsChoice: Bool

    private init() {
        if let raw = UserDefaults.standard.string(forKey: Self.storageKey),
           let lang = AppLanguage(rawValue: raw) {
            language = lang
            needsChoice = false
        } else {
            // Pre-select from the system preference, but still prompt once so the
            // user can override (a Chinese user on an English Mac, or vice versa).
            let sys = Locale.preferredLanguages.first ?? "en"
            language = sys.hasPrefix("zh") ? .zh : .en
            needsChoice = true
        }
    }

    /// Confirm the first-launch choice (also used by Settings → Language).
    func choose(_ lang: AppLanguage) {
        language = lang
        needsChoice = false
    }

    /// Translate an English source string for the current language.
    func tr(_ key: String) -> String {
        language == .zh ? (Localization.zh[key] ?? key) : key
    }
}

/// Global shorthand used throughout the UI, e.g. `Text(L("Settings"))` or
/// `Button(L("Add Game")) { … }`. Returns a plain `String`, so it slots into any
/// API that takes a `String` (Text, Button title, .help, navigationTitle, …).
func L(_ key: String) -> String { LocalizationManager.shared.tr(key) }

/// English-source → 中文 translations. Keys are the EXACT English UI strings.
///
/// Glossary — keep these consistent across the app:
///   Bottle = 容器   Game = 游戏   Library = 游戏库   Launch = 启动
///   Settings = 设置   Installer = 安装程序   Install = 安装   Backend = 后端
///   Prefix = 前缀   Wine = Wine (untranslated)   Steam = Steam (untranslated)
///   Backend names (DXVK, DXMT, D3DMetal, GPTK, Monado, OpenXR) stay as-is.
enum Localization {
    static let zh: [String: String] = [
        // ── Common / chrome ──────────────────────────────────────────────
        "Settings": "设置",
        "Language": "语言",
        "Cancel": "取消",
        "OK": "确定",
        "Done": "完成",
        "Close": "关闭",
        "Save": "保存",
        "Delete": "删除",
        "Remove": "移除",
        "Add": "添加",
        "Open": "打开",
        "Launch": "启动",
        "Library": "游戏库",
        "Store": "商店",
        "Search games": "搜索游戏",
        "Search showcase": "搜索精选",
        "MacNCheese": "MacNCheese",

        // ── Bottles / sidebar ────────────────────────────────────────────
        "Bottles": "容器",
        "Create Bottle": "创建容器",
        "New Bottle": "新建容器",
        "New Bottle…": "新建容器…",
        "No bottle selected": "未选择容器",
        "Create a bottle to get started.": "创建一个容器以开始。",
        "Show in Finder": "在访达中显示",
        "Move to Front": "移到最前",
        "Move to Back": "移到最后",
        "Kill Wineserver": "终止 Wineserver",
        "This bottle's drive isn't connected.": "该容器的驱动器未连接。",

        // ── Library / games ──────────────────────────────────────────────
        "Loading your library…": "正在加载游戏库…",
        "Drive Not Connected": "驱动器未连接",
        "\"%@\" is on the drive \"%@\", which isn't connected. Reconnect it to continue.": "“%@”位于驱动器“%@”上，该驱动器未连接。请重新连接以继续。",
        "Can't find this bottle's folder. If it's on an external drive, reconnect it.": "找不到该容器的文件夹。如果它位于外部驱动器上，请重新连接。",
        "Can't find this bottle's launcher. If it's on an external drive, reconnect it.": "找不到该容器的启动器。如果它位于外部驱动器上，请重新连接。",
        "Try Again": "重试",
        "This game's files aren't available — its drive isn't connected.": "无法访问该游戏的文件——其驱动器未连接。",
        "This app's files aren't available — its drive isn't connected.": "无法访问该程序的文件——其驱动器未连接。",
        "No Games": "暂无游戏",
        "Add a game or run an installer to get started.": "添加游戏或运行安装程序以开始。",
        "Run Installer": "运行安装程序",
        "Add Game": "添加游戏",
        "Launch Options…": "启动选项…",
        "Remove from Library": "从游戏库中移除",
        "Removes the game from this list only — no files are deleted.": "仅将游戏从列表中移除——不会删除任何文件。",
        "Re-scan the bottle for games.": "重新扫描容器中的游戏。",
        "Select Game EXE": "选择游戏 EXE",

        // ── Wine lifecycle (leak fixes) ──────────────────────────────────
        "This game is already running. If it's frozen, press the red stop button (Kill Wineserver), then launch again.": "该游戏已在运行。如果它已无响应，请按红色停止按钮（终止 Wineserver）后重新启动。",
        "Wine is still running": "Wine 仍在运行",
        "Games or Wine processes started by MacNCheese are still running. Quit them too?": "由 MacNCheese 启动的游戏或 Wine 进程仍在运行。要一并退出吗？",
        "Quit Wine & Exit": "退出 Wine 并关闭",
        "Leave Running & Exit": "保留运行并关闭",
        "Remember my choice": "记住我的选择",
        "On Quit": "退出时",
        "Ask every time": "每次询问",
        "Quit all Wine processes": "退出所有 Wine 进程",
        "Leave Wine running": "保留 Wine 运行",
        "What happens to running games and Wine when you close MacNCheese.": "关闭 MacNCheese 时如何处理正在运行的游戏和 Wine。",
        "Power": "电源",
        "Stop background Steam when no game is running": "无游戏运行时停止后台 Steam",
        "Background Steam keeps using CPU after games quit; this stops it after 5 idle minutes. Steam you opened yourself is never touched.": "后台 Steam 在游戏退出后仍持续占用 CPU；空闲 5 分钟后将自动停止。你自己打开的 Steam 不受影响。",

        // ── Launcher / Steam ─────────────────────────────────────────────
        "Steam not launching? Run this simple fix!": "Steam 无法启动？运行这个简单修复！",
        "Downloads the latest MacNCheese Wine and re-runs wineboot on this bottle.": "下载最新的 MacNCheese Wine 并在此容器上重新运行 wineboot。",
        "Run Fix": "运行修复",
        "Return to Library": "返回游戏库",
        "Open Steam": "打开 Steam",
        "Close Steam": "关闭 Steam",
        "Open Launcher": "打开启动器",
        "Close Launcher": "关闭启动器",

        // ── First-run language popup ─────────────────────────────────────
        "Choose your language": "选择你的语言",
        "You can change this later in Settings → Language.": "你之后可以在“设置 → 语言”中更改。",
        "Continue": "继续",
        "Choose the language for the MacNCheese interface.": "选择 MacNCheese 界面的语言。",
        "Changes apply immediately.": "更改立即生效。",

        // ── Settings tabs ────────────────────────────────────────────────
        "Bottle": "容器",
        "Paths": "路径",
        "Setup": "安装设置",
        "Diagnose": "诊断",
        "Logs": "日志",

        // (Per-view strings are merged in below.)

        // ── Merged per-view translations (agents + manual) ──────────────
        "App ID: %@": "App ID：%@",
        "Steam Description": "Steam 简介",
        "Loading from Steam...": "正在从 Steam 加载…",
        "No Steam description available.": "暂无 Steam 简介。",
        "EXE:": "EXE：",
        "Scanning...": "扫描中…",
        "Auto-detect": "自动检测",
        "Browse…": "浏览…",
        "Graphics Engine:": "图形后端：",
        "Detecting...": "检测中…",
        "— Experimental —": "— 实验性 —",
        "Args:": "参数：",
        "Optional launch arguments…": "可选启动参数…",
        "Retina hi-res mode": "Retina 高分辨率模式",
        "Enable high resolution for retina screens. Game compatibility might be affected.": "为 Retina 屏幕启用高分辨率。可能影响游戏兼容性。",
        "Metal HUD": "Metal HUD",
        "Advanced debug (verbose logs)": "高级调试（详细日志）",
        "Runs with WINEDEBUG=+loaddll,+module,+seh instead of -all (shows DLL load failures, missing imports, crashes) and adds -log for Unreal games. Use this when a game won't start, then check the per-game log in ~/Library/Logs/MacNCheese.": "使用 WINEDEBUG=+loaddll,+module,+seh 替代 -all 运行（显示 DLL 加载失败、缺失导入、崩溃信息），并为 Unreal 游戏添加 -log。在游戏无法启动时使用，日志位于 ~/Library/Logs/MacNCheese。",
        "Environment Variables:": "环境变量：",
        "Advertise AVX2 / FMA / F16C": "公开 AVX2 / FMA / F16C",
        "Sets ROSETTA_ADVERTISE_AVX=1 so Rosetta exposes AVX2/FMA/F16C. Required by some AAA titles (e.g. God of War Ragnarök). Needs macOS 15+.": "设置 ROSETTA_ADVERTISE_AVX=1 让 Rosetta 公开 AVX2/FMA/F16C。部分 AAA 游戏（如《战神：诸神黄昏》）需要此选项。需 macOS 15+。",
        "KEY=value, one per line. Saved per game. Combined with the AVX toggle above.": "KEY=value 格式，每行一条。按游戏保存，与上方 AVX 开关合并生效。",
        "Synchronization:": "同步方式：",
        "Enable ESync": "启用 ESync",
        "Enable MSync": "启用 MSync",
        "MSync is macOS-specific and usually should not be combined with ESync.": "MSync 为 macOS 专用，通常不应与 ESync 同时启用。",
        "Silent Steam": "静默 Steam",
        "No Steam": "不使用 Steam",
        "Silent: background Steam (no window) — best for Steamworks games like cs2. Open: full Steam UI. No Steam: don't launch Steam — best for standalone UE5/Unity games where background Steam interferes.": "静默：Steam 在后台运行（无窗口），适合 cs2 等 Steamworks 游戏。打开：显示完整 Steam 界面。不使用 Steam：不启动 Steam，适合后台 Steam 会干扰的独立 UE5/Unity 游戏。",
        "Play": "开始游戏",
        "Auto (recommended)": "自动（推荐）",
        "Wine Devel (OpenGL games)": "Wine Devel（OpenGL 游戏）",
        "DXMT (Balanced)": "DXMT（均衡）",
        "DXMT + OpenXR (VR, monofunc)": "DXMT + OpenXR（VR，monofunc）",
        "D3DMetal (Best Performance)": "D3DMetal（最佳性能）",
        "DXVK (Best Compatibility)": "DXVK（最佳兼容性）",
        "VKD3D-Proton (D3D12)": "VKD3D-Proton（D3D12）",
        "Wine Builtin": "Wine 内置",
        "Mesa llvmpipe (CPU)": "Mesa llvmpipe（CPU）",
        "Mesa Zink (Vulkan)": "Mesa Zink（Vulkan）",
        "Mesa SWR (CPU/AVX)": "Mesa SWR（CPU/AVX）",
        "GPTK (D3DMetal, copy DLLs)": "GPTK（D3DMetal，复制 DLL）",
        "GPTK Full (Apple Toolkit)": "GPTK 完整版（Apple 工具包）",
        "New Announcement": "新公告",
        "Open Store": "打开商店",
        "Open Epic Games Store": "打开 Epic 游戏商店",
        "Kill Wineserver?": "终止 Wineserver？",
        "Kill": "终止",
        "This will forcefully terminate all Wine processes in the current bottle. Any unsaved game progress will be lost.": "这将强制终止当前容器中的所有 Wine 进程。未保存的游戏进度将会丢失。",
        "Launch to browse your games.": "启动以浏览你的游戏。",
        "Launch Steam to browse and install games.": "启动 Steam 以浏览和安装游戏。",
        "Close %@": "关闭 %@",
        "Open %@": "打开 %@",
        "Launch %@": "启动 %@",
        "Compatibility List Update": "兼容性列表更新",
        "The compatibility list may have changed. Launch the launcher anyway?": "兼容性列表可能已更改。是否仍要启动？",
        "Dismiss": "忽略",
        "Fetching your library…": "正在获取游戏库…",
        "No games found": "未找到游戏",
        "UPDATE": "更新",
        "Launching…": "正在启动…",
        "Update": "更新",
        "%@%% — Paused": "%@%% — 已暂停",
        "Queue #%@": "队列 #%@",
        "Installing…": "正在安装…",
        "Download": "下载",
        "Launch options for %@": "%@ 的启动选项",
        "Resume Download": "继续下载",
        "Cancel Download": "取消下载",
        "Download & Install": "下载并安装",
        "Pause Download": "暂停下载",
        "%@%%": "%@%%",
        "EPIC GAMES": "EPIC GAMES",
        "Preparing Epic Games support…": "正在准备 Epic Games 支持…",
        "Checking your Epic Games account…": "正在检查你的 Epic Games 账户…",
        "Connect your Epic Games account to access your library.": "连接你的 Epic Games 账户以访问游戏库。",
        "Signing in…": "正在登录…",
        "Connect": "连接",
        "Authentication failed. Please try again.": "认证失败，请重试。",
        "Sign in to Epic Games": "登录 Epic Games",
        "Epic Games Store": "Epic Games 商店",
        "MacNCheese Store": "MacNCheese 商店",
        "Game Showcase": "精选游戏",
        "Downloads": "下载",
        "Discussions": "讨论",
        "Issues": "问题",
        "Pull Requests": "拉取请求",
        "Insights": "数据洞察",
        "Release": "版本",
        "Open on GitHub": "在 GitHub 上打开",
        "Loading...": "加载中…",
        "total downloads": "总下载次数",
        "Published %@": "发布于 %@",
        "Per asset": "各文件",
        "Open all on GitHub →": "在 GitHub 上查看全部 →",
        "Insights & Traffic": "洞察与流量",
        "GitHub's Insights and Traffic data require repo push access to view via API.\nOpen it on GitHub instead.": "GitHub 的洞察与流量数据需要仓库推送权限才能通过 API 查看。\n请直接在 GitHub 上查看。",
        "Open Insights on GitHub": "在 GitHub 上查看洞察",
        "Loading showcase…": "加载精选中…",
        "Retry": "重试",
        "No showcased games yet": "暂无精选游戏",
        "Posts from the community Game Showcase channel will appear here.": "社区游戏精选频道的内容将在此处显示。",
        "No games match “%@”": "没有与「%@」匹配的游戏",
        "Try a different search.": "请尝试其他搜索词。",
        "%@ comment": "%@ 条评论",
        "%@ comments": "%@ 条评论",
        "View on Discord →": "在 Discord 上查看 →",
        "Unknown": "未知",
        "Open full image": "查看原图",
        "Delete Bottle": "删除容器",
        "This will forcefully stop all Wine processes for \"%@\".": "这将强制停止「%@」的所有 Wine 进程。",
        "Delete Bottle?": "删除容器？",
        "This will permanently delete \"%@\" and all its contents.": "这将永久删除「%@」及其所有内容。",
        "Create a Bottle": "创建容器",
        "Bottle Name": "容器名称",
        "e.g. My Games": "例如：我的游戏",
        "Launcher": "启动器",
        "Epic Games": "Epic 游戏",
        "None (plain Wine)": "无（纯 Wine）",
        "Steam will be used to manage and launch games.": "将使用 Steam 管理和启动游戏。",
        "Epic Games library via Legendary. Connect your account after creation.": "通过 Legendary 使用 Epic 游戏库，创建后连接你的账户。",
        "No launcher – add games manually.": "无启动器 – 手动添加游戏。",
        "Custom location": "自定义位置",
        "Path": "路径",
        "Select": "选择",
        "Create": "创建",
        "DXVK source": "DXVK 源目录",
        "DXVK install (64-bit)": "DXVK 安装目录（64 位）",
        "DXVK install (32-bit)": "DXVK 安装目录（32 位）",
        "SteamSetup.exe": "SteamSetup.exe",
        "Mesa x64 dir": "Mesa x64 目录",
        "DXMT dir": "DXMT 目录",
        "VKD3D-Proton dir": "VKD3D-Proton 目录",
        "GPTK dir": "GPTK 目录",
        "Select SteamSetup.exe": "选择 SteamSetup.exe",
        "Move Prefix": "移动前缀",
        "Move": "移动",
        "Open Log Folder": "打开日志文件夹",
        "Log file:": "日志文件：",
        "No log content. Launch a game first.": "暂无日志内容，请先启动游戏。",
        "Auto-refresh": "自动刷新",
        "... (%@ lines truncated) ...": "……（已省略 %@ 行）……",
        "Failed to read log: %@": "读取日志失败：%@",
        "Updating to %@…": "正在更新至 %@…",
        "Working…": "处理中…",
        "Update failed": "更新失败",
        "Use “Release notes” to update manually.": "请使用「发行说明」手动更新。",
        "Update available: %@": "有可用更新：%@",
        "You're on %@": "当前版本：%@",
        "Update & Restart": "更新并重启",
        "Release notes": "发行说明",
        "MacNCheese Announcement": "MacNCheese 公告",
        "Posted %@": "发布于 %@",
        "Read on GitHub": "在 GitHub 上阅读",
        "Don't show again": "不再显示",
        "Got it": "知道了",
        "Starting…": "正在启动…",
        "Couldn't start update": "无法启动更新",
        "Restarting…": "正在重启…",
        "Failed to start backend: %@": "无法启动后端：%@",
        "Failed to load bottles: %@": "无法加载容器：%@",
        "Failed to scan games: %@": "无法扫描游戏：%@",
        "Failed to launch game: %@": "无法启动游戏：%@",
        "Failed to launch: %@": "无法启动：%@",
        "Failed to launch Steam: %@": "无法启动 Steam：%@",
        "Failed to create bottle: %@": "无法创建容器：%@",
        "Failed to reorder bottles: %@": "无法重新排序容器：%@",
        "Failed to delete bottle: %@": "无法删除容器：%@",
        "Failed to kill wineserver: %@": "无法终止 Wine 服务：%@",
        "Failed to init prefix: %@": "无法初始化前缀：%@",
        "Failed to clean prefix: %@": "无法清理前缀：%@",
        "Failed to run exe: %@": "无法运行程序：%@",
        "Failed to open folder: %@": "无法打开文件夹：%@",
        "Failed to get bottle config: %@": "无法获取容器配置：%@",
        "Failed to save config: %@": "无法保存配置：%@",
        "Failed to save game order: %@": "无法保存游戏排序：%@",
        "Failed to save game config: %@": "无法保存游戏配置：%@",
        "Failed to add game: %@": "无法添加游戏：%@",
        "Failed to remove game: %@": "无法移除游戏：%@",
        "Failed to get components status: %@": "无法获取组件状态：%@",
        "Failed to start update: %@": "无法启动更新：%@",
        "Failed to get install progress: %@": "无法获取安装进度：%@",
        "Failed to get update info: %@": "无法获取更新信息：%@",
        "Failed to list backends: %@": "无法列出后端：%@",
        "Failed to open winecfg: %@": "无法打开 Wine 配置：%@",
        "Failed to move bottle: %@": "无法移动容器：%@",
        "Diagnosis failed: %@": "诊断失败：%@",
        "Repair failed: %@": "修复失败：%@",
        "Failed to start installer: %@": "无法启动安装器：%@",
        "Failed to detect exes: %@": "无法检测可执行文件：%@",
        "Failed to queue install: %@": "无法加入安装队列：%@",
        "Failed to launch %@: %@": "无法启动 %@：%@",
        "Failed to get status: %@": "无法获取状态：%@",
        "Auto (prefer Stable)": "自动（优先 Stable）",
        "Browse": "浏览",
        "Builds Monado as an x86_64 OpenXR runtime and registers it. The wineopenxr bridge forwards D3D11 OpenXR to this runtime, which is loaded into the x86_64 (Rosetta) Wine process — so it MUST be x86_64. Without this, an arm64 system Monado fails with 'incompatible architecture' and VR won't start. Builds with cmake + the x86_64 Homebrew Vulkan/MoltenVK deps (slow).": "构建 x86_64 版 Monado OpenXR 运行时并注册。wineopenxr 桥接会将 D3D11 OpenXR 转发给该运行时，而它会被加载进 x86_64（Rosetta）Wine 进程，因此必须是 x86_64。否则 arm64 的系统 Monado 会因「incompatible architecture」而无法加载，VR 无法启动。使用 cmake 与 x86_64 Homebrew 的 Vulkan/MoltenVK 依赖构建（较慢）。",
        "Builds monofunc/dxmt (feature/openxr) — DXMT's Metal D3D11/10 translation plus OpenXR passthrough — with meson + mingw-w64 + llvm@15. Installs it as the \"DXMT + OpenXR (VR)\" graphics backend and pulls in wineopenxr so D3D11 VR apps reach the native macOS OpenXR runtime. Set DXMT_OPENXR_URL to install a prebuilt build instead.": "使用 meson + mingw-w64 + llvm@15 构建 monofunc/dxmt（feature/openxr）——DXMT 的 Metal D3D11/10 转换加 OpenXR 透传。安装为「DXMT + OpenXR (VR)」图形后端，并一并安装 wineopenxr，使 D3D11 VR 应用能访问原生 macOS OpenXR 运行时。也可设置 DXMT_OPENXR_URL 安装预编译版本。",
        "Checking components...": "正在检查组件…",
        "Checks": "检查项",
        "Clean Prefix": "清理前缀",
        "Clones monofunc/wineopenxr, builds it (needs cmake + mingw-w64), and registers it as the active OpenXR runtime so D3D11 OpenXR apps can talk to a native macOS OpenXR runtime via DXMT.": "克隆并构建 monofunc/wineopenxr（需要 cmake + mingw-w64），并将其注册为活动 OpenXR 运行时，使 D3D11 OpenXR 应用能通过 DXMT 访问原生 macOS OpenXR 运行时。",
        "Custom icon (PNG)": "自定义图标（PNG）",
        "DXMT": "DXMT",
        "DXMT (%@)": "DXMT（%@）",
        "DXMT + OpenXR fork (monofunc/dxmt, builds from source)": "DXMT + OpenXR 分支（monofunc/dxmt，从源码构建）",
        "DXVK": "DXVK",
        "Delete Prefix": "删除前缀",
        "Diagnose Cheese": "诊断 Cheese",
        "Display name": "显示名称",
        "Done!": "完成！",
        "Everything": "全部",
        "Finished with errors": "完成但有错误",
        "Force stop all Wine processes": "强制停止所有 Wine 进程",
        "Generated %@": "生成于 %@",
        "Graphics": "图形",
        "Initialize Prefix": "初始化前缀",
        "Install or repair Steam": "安装或修复 Steam",
        "Installed": "已安装",
        "Launcher exe": "启动器 exe",
        "Leave empty for Steam (default)": "留空则使用 Steam（默认）",
        "Leave empty for default": "留空则使用默认",
        "Mesa": "Mesa",
        "Minimal": "最小",
        "Monado OpenXR runtime (x86_64, builds from source)": "Monado OpenXR 运行时（x86_64，从源码构建）",
        "Move this bottle folder": "移动此容器文件夹",
        "No-shim patched Wine 11.0 + Apple D3DMetal. Removes the gs.base swap so D3D11/12 games talk to Apple's D3DMetal framework with no DYLD shim — powers the D3DMetal launch engine. Bundled with the app; unzips on install.": "免 shim 修补版 Wine 11.0 + Apple D3DMetal。移除 gs.base 替换，使 D3D11/12 游戏无需 DYLD shim 直接调用 Apple 的 D3DMetal 框架——驱动 D3DMetal 启动引擎。随应用打包，安装时解压。",
        "None": "无",
        "Open SteamSetup": "打开 SteamSetup",
        "Open Wine configuration": "打开 Wine 配置",
        "Open in Finder": "在访达中打开",
        "Permanently remove from disk": "从磁盘永久删除",
        "Prefix Tools": "前缀工具",
        "Prefix path": "前缀路径",
        "Quick Setup": "快速配置",
        "Recommended": "推荐",
        "Refresh": "刷新",
        "Repair complete": "修复完成",
        "Repair finished with errors": "修复完成但有错误",
        "Repair running...": "修复进行中…",
        "Run": "运行",
        "Run Diagnosis": "运行诊断",
        "Run Repair?": "运行修复？",
        "Run a diagnosis to scan for missing components, corrupted Wine files and prefix loader failures.": "运行诊断以扫描缺失组件、损坏的 Wine 文件及前缀加载故障。",
        "Run wineboot -u to update": "运行 wineboot -u 进行更新",
        "Run wineboot to create drive_c": "运行 wineboot 创建 drive_c",
        "Save Changes": "保存更改",
        "Scanning": "扫描中",
        "Scanning MacNCheese, Wine and the selected prefix...": "正在扫描 MacNCheese、Wine 及所选前缀…",
        "Select a bottle in the sidebar to configure it.": "在侧边栏选择一个容器进行配置。",
        "Select all components": "选择全部组件",
        "Select: Tools, Wine Stable, DXVK, Mesa": "选择：工具、Wine Stable、DXVK、Mesa",
        "Show prefix folder": "显示前缀文件夹",
        "Stable": "Stable",
        "Staging": "Staging",
        "Standalone Wine Staging 11.8 with the OpenGL 3.2+ macdrv patch, for SDL3/OpenGL games (e.g. Mewgenics). Downloaded on install. Independent build.": "独立的 Wine Staging 11.8，含 OpenGL 3.2+ macdrv 补丁，适用于 SDL3/OpenGL 游戏（如 Mewgenics）。安装时下载，独立构建。",
        "Starting...": "正在启动…",
        "Suggested Repairs": "建议的修复",
        "Tools": "工具",
        "Tools (git, 7z, wget)": "工具（git、7z、wget）",
        "Update available": "有可用更新",
        "VKD3D-Proton": "VKD3D-Proton",
        "VR": "VR",
        "Wine": "Wine",
        "Wine (Stable)": "Wine（Stable）",
        "Wine (Staging — %@)": "Wine（Staging — %@）",
        "Wine (Staging)": "Wine（Staging）",
        "Wine (Translation Engine)": "Wine（转换引擎）",
        "Wine D3DMetal (shimless, ~888 MB)": "Wine D3DMetal（免 shim，约 888 MB）",
        "Wine Unified (Steam + games engine)": "Wine 统一引擎（Steam + 游戏）",
        "One patched Wine 11.0 that renders Steam via DXMT and routes games to the chosen backend (D3DMetal/DXMT/DXVK). This is the default engine. Bundled with the app; installs into deps.": "一个修补过的 Wine 11.0 通过 DXMT 渲染 Steam 并把游戏路由到所选后端（D3DMetal/DXMT/DXVK）。这是默认引擎。随应用打包安装到 deps。",
        "Wine Devel (SDL3/OpenGL, ~310 MB)": "Wine Devel（SDL3/OpenGL，约 310 MB）",
        "Wine Logs": "Wine 日志",
        "Winecfg": "Winecfg",
        "wineopenxr (D3D11 OpenXR bridge, builds from source)": "wineopenxr（D3D11 OpenXR 桥接，从源码构建）",

        // ── First-run onboarding installer ───────────────────────────────
        "Welcome to MacNCheese": "欢迎使用 MacNCheese",
        "Play Windows games on your Mac.": "在你的 Mac 上畅玩 Windows 游戏。",
        "Checking what's already installed…": "正在检查已安装的组件…",
        "MacNCheese will set up everything it needs to run games:": "MacNCheese 将安装运行游戏所需的全部组件：",
        "Runs Windows apps and games.": "运行 Windows 应用和游戏。",
        "Graphics (DXVK + Mesa)": "图形（DXVK + Mesa）",
        "DirectX-to-Vulkan translation for 3D games.": "为 3D 游戏提供 DirectX 到 Vulkan 的转换。",
        "git, 7-Zip and wget used during setup.": "安装过程中使用的 git、7-Zip 和 wget。",
        "Also install advanced graphics": "同时安装高级图形组件",
        "Wine Staging, DXMT and VKD3D-Proton. Larger download.": "Wine Staging、DXMT 和 VKD3D-Proton。下载更大。",
        "This downloads a few hundred MB the first time and may take several minutes.": "首次安装需下载数百 MB，可能需要几分钟。",
        "Install & Get Started": "安装并开始",
        "Skip for now": "暂时跳过",
        "You're all set!": "一切就绪！",
        "Wine and graphics support are already installed.": "Wine 和图形支持已安装。",
        "Get Started": "开始使用",
        "Setup finished with some errors": "安装完成，但存在一些错误",
        "Setup complete!": "安装完成！",
        "You can finish anyway and retry missing pieces later in Settings → Setup.": "你仍可继续，稍后在「设置 → 安装设置」中重试缺失的组件。",
        "installer.sh not found — reinstall MacNCheese.": "未找到 installer.sh——请重新安装 MacNCheese。",

        // ── Bottle tab Wine selector ─────────────────────────────────────
        "Picks the best installed Wine.": "自动选择最合适的已安装 Wine。",
        "Using %@%@": "正在使用 %@%@",
        "Using %@": "正在使用 %@",
        "Using a detected Wine build.": "正在使用检测到的 Wine 版本。",
        "No Wine installed yet — install one below.": "尚未安装 Wine——请在下方安装。",
        "Re-scan installed Wine": "重新扫描已安装的 Wine",
        "Automatic": "自动",
        "Wine Stable": "Wine Stable",
        "Wine Staging": "Wine Staging",
        "Wine Devel": "Wine Devel",
        "Not installed": "未安装",
        "Install": "安装",
        "Failed to detect Wine: %@": "检测 Wine 失败：%@",

        // ── Game launch sheet / detail page ──────────────────────────────
        "Starting Steam…": "正在启动 Steam…",
        "Launch game": "启动游戏",
        "Description of the game": "游戏简介",
        "Wine & Graphics": "Wine 与图形",
        "Screenshots": "游戏截图",

        // ── Onboarding: Wine D3DMetal + Steam guide ──────────────────────
        "Wine D3DMetal": "Wine D3DMetal",
        "Apple's high-performance Direct3D engine.": "Apple 高性能 Direct3D 引擎。",
        "Install Steam": "安装 Steam",
        "Last step: install Steam.": "最后一步：安装 Steam。",
        "Pick your SteamSetup.exe — MacNCheese will create a ready-to-play Steam bottle and run the installer.": "选择你的 SteamSetup.exe——MacNCheese 将创建一个可直接使用的 Steam 容器并运行安装程序。",
        "Don't have it? Download SteamSetup.exe from store.steampowered.com/about, then choose it here.": "没有？请从 store.steampowered.com/about 下载 SteamSetup.exe，然后在此选择。",
        "Setting up Steam…": "正在设置 Steam…",
        "Choose SteamSetup.exe…": "选择 SteamSetup.exe…",
        "Steam is installing": "Steam 正在安装",
        "Follow the Steam Setup window to finish, then come back here. A \"Steam\" bottle is ready in your library.": "请按 Steam 安装窗口完成安装，然后返回此处。「Steam」容器已在你的游戏库中就绪。",
        "Finish": "完成",
    ]
}

/// Settings → Language tab: switch the UI language at any time. The change is
/// live (the main window re-renders via `.id(loc.language)`; this tab re-renders
/// because it observes the manager).
struct LanguageSettingsTab: View {
    @EnvironmentObject var loc: LocalizationManager

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(L("Language"))
                    .font(.headline)
                Text(L("Choose the language for the MacNCheese interface."))
                    .font(.callout)
                    .foregroundStyle(.secondary)

                Picker(L("Language"), selection: $loc.language) {
                    ForEach(AppLanguage.allCases) { lang in
                        Text(lang.displayName).tag(lang)
                    }
                }
                .pickerStyle(.radioGroup)
                .labelsHidden()

                Text(L("Changes apply immediately."))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// First-launch popup to pick the UI language. The same choice is available
/// later via Settings → Language. Buttons always show each language's NATIVE
/// name so they're readable whatever the current language is.
struct LanguagePickerSheet: View {
    @EnvironmentObject var loc: LocalizationManager
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 18) {
            Image(systemName: "globe")
                .font(.system(size: 46))
                .foregroundStyle(Color.brand)
            Text(L("Choose your language"))
                .font(.title2).fontWeight(.bold)
            Text("中文 · English")
                .foregroundStyle(.secondary)

            HStack(spacing: 14) {
                ForEach(AppLanguage.allCases) { lang in
                    Button {
                        // First-run flow: language → onboarding installer.
                        // ContentView presents OnboardingView once needsChoice
                        // flips false (see ContentView.evaluateOnboarding).
                        loc.choose(lang)
                        dismiss()
                    } label: {
                        Text(lang.displayName)
                            .frame(minWidth: 110)
                            .padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(loc.language == lang ? Color.brand : Color.secondary)
                }
            }
            .padding(.top, 4)

            Text(L("You can change this later in Settings → Language."))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(32)
        .frame(width: 400)
        .interactiveDismissDisabled()
    }
}
