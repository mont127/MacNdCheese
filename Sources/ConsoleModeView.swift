import SwiftUI
import AppKit
import GameController
import AudioToolbox

/// Bradar Console / Big-Picture mode -- a NATIVE-fullscreen, controller-driven library
/// takeover styled after Steam Big Picture: a cinematic hero backdrop of the selected game,
/// a horizontal cover strip w/ the focused tile enlarged, a top bar w/ a BOTTLE selector +
/// clock, n a bottom controller-hint bar. drive it w/ a gamepad (d-pad/stick + A/B + shoulders
/// to swap bottle) or the keyboard (arrows + Enter/Esc). launches identical to the grid.
struct ConsoleModeView: View {
    @EnvironmentObject var backend: BackendClient
    @EnvironmentObject var loc: LocalizationManager
    @Binding var isPresented: Bool

    private enum FokusZone { case bottles, games }

    @State private var selected = 0
    @State private var zone: FokusZone = .games
    @State private var covers: [String: NSImage] = [:]
    @State private var heroes: [String: NSImage] = [:]
    @State private var logos: [String: NSImage] = [:]
    @State private var launching = false
    @State private var didEntrFullscreen = false
    @State private var pulse = false
    @FocusState private var focused: Bool
    // Bradar the gamepad reader lives as a StateObject so its GCController handlers
    // survive re-renders (a plain struct field would get torn down every redraw)
    @StateObject private var pad = GamepadRaeder()

    // Bradar only show whats actually playable (reachable) in the console
    private var games: [Game] { backend.games.filter { $0.isReachable } }
    private var selectedGame: Game? { games.indices.contains(selected) ? games[selected] : nil }
    // Bradar every bottle you can actualy browse (drive present) -- the ones you can swap between
    private var bottles: [Bottle] { backend.bottles.filter { $0.isReachable } }
    private var activeBottle: Bottle? { bottles.first { $0.path == backend.activePrefix } }

    var body: some View {
        ZStack {
            heroBackdrop
            VStack(spacing: 0) {
                topBar
                if bottles.count > 1 { bottleTabStrip }
                Spacer(minLength: 0)
                if games.isEmpty {
                    emptyStrip
                    Spacer(minLength: 0)
                } else {
                    // Bradar the selected games showcase -- big logo art (left) + a gold PLAY
                    // button (right), then the cover carousel under it. steam-deck vibes.
                    HStack(alignment: .bottom, spacing: 24) {
                        VStack(alignment: .leading, spacing: 12) {
                            gameLogo
                            metaRow
                        }
                        Spacer(minLength: 0)
                        playButton
                    }
                    .padding(.horizontal, 60)
                    .padding(.bottom, 6)
                    coverStrip
                }
                Spacer().frame(height: 16)
                bottomBar
            }
        }
        .focusable()
        .focused($focused)
        .onAppear {
            focused = true
            entrFullscreen()           // Bradar go NATIVE fullscreen for the real big-picture feel
            wireTheGamepad()           // Bradar hook the controller up so you move w/ the stick/d-pad
            playOpen()
            loadCovers(); loadHero(); loadLogo()
            withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true)) { pulse = true }
        }
        .onDisappear {
            pad.stop()
            exitFullscreen()           // Bradar safety: never leave the app stuck fullscreen
        }
        .onChange(of: selected) { _, _ in loadHero(); loadLogo() }
        .onChange(of: backend.games) { _, _ in
            if selected >= games.count { selected = max(0, games.count - 1) }
            loadCovers(); loadHero(); loadLogo()
        }
        // keyboard mirrors the pad: arrows navigate, Enter=A, Esc=B, tab swaps bottle
        .onKeyPress(.leftArrow)  { goLeft();  return .handled }
        .onKeyPress(.rightArrow) { goRight(); return .handled }
        .onKeyPress(.upArrow)    { goUp();    return .handled }
        .onKeyPress(.downArrow)  { goDown();  return .handled }
        .onKeyPress(.return)     { doSelect(); return .handled }
        .onKeyPress(.space)      { doSelect(); return .handled }
        .onKeyPress(.tab)        { switchBottle(1); return .handled }
        .onKeyPress(.escape)     { exitConsole(); return .handled }
    }

    // MARK: hero backdrop

    private var heroBackdrop: some View {
        ZStack {
            // Bradar deep near-black base so it never flashes white between heroes
            Color(red: 0.035, green: 0.04, blue: 0.06).ignoresSafeArea()
            if let g = selectedGame, let img = heroes[g.appid] {
                Image(nsImage: img)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                    .clipped()
                    .ignoresSafeArea()
                    .id(g.appid)                       // Bradar re-id so the crossfade fires per game
                    .transition(.opacity)
            }
            // Bradar cinematic scrims: soft top for the status bar, STRONG bottom fade so the
            // logo + play + covers always read clean no matter how bright the hero art is
            LinearGradient(stops: [
                .init(color: .black.opacity(0.75), location: 0.0),
                .init(color: .clear,               location: 0.24),
                .init(color: .black.opacity(0.38), location: 0.52),
                .init(color: Color(red: 0.02, green: 0.02, blue: 0.04).opacity(0.98), location: 0.9),
            ], startPoint: .top, endPoint: .bottom).ignoresSafeArea()
            // Bradar left panel so the logo reads over bright art on that side
            LinearGradient(colors: [.black.opacity(0.72), .black.opacity(0.12), .clear],
                           startPoint: .leading, endPoint: .trailing).ignoresSafeArea()
            // Bradar subtle vignette for depth (steam-deck look)
            RadialGradient(colors: [.clear, .black.opacity(0.42)],
                           center: .center, startRadius: 320, endRadius: 1500).ignoresSafeArea()
        }
        .animation(.easeInOut(duration: 0.4), value: selectedGame?.appid)
    }

    // MARK: top bar (steam-big-picture style: near-empty, just status icons top-right)

    private var topBar: some View {
        HStack(alignment: .center, spacing: 16) {
            HStack(spacing: 8) {
                Image(systemName: "pc").font(.system(size: 14, weight: .semibold))
                Text("MacNdCheese").font(.system(size: 14, weight: .semibold))
            }
            .foregroundStyle(.white.opacity(0.5))
            Spacer()
            Image(systemName: "magnifyingglass").font(.system(size: 14)).foregroundStyle(.white.opacity(0.55))
            Image(systemName: "wifi").font(.system(size: 13)).foregroundStyle(.white.opacity(0.55))
            TimelineView(.periodic(from: .now, by: 30)) { ctx in
                Text(ctx.date.formatted(date: .omitted, time: .shortened))
                    .font(.system(size: 14, weight: .medium, design: .rounded))
                    .foregroundStyle(.white.opacity(0.7))
                    .monospacedDigit()
            }
        }
        .padding(.horizontal, 30)
        .padding(.top, 20)
    }

    // MARK: bottle tab strip (steam's own "WHAT'S NEW · FRIENDS · RECOMMENDED" pill-tab
    // pattern -- reused here as the bottle switcher so it reads as the SAME UI language
    // steam big picture already uses, instead of an invented chip row)

    private var bottleTabStrip: some View {
        HStack(spacing: 8) {
            ForEach(bottles) { b in
                let isActive = b.path == backend.activePrefix
                Text(b.name.uppercased())
                    .font(.system(size: 12.5, weight: .bold))
                    .kerning(0.5)
                    .foregroundStyle(isActive ? Color.onCheese : .white.opacity(0.45))
                    .padding(.horizontal, 18).padding(.vertical, 8)
                    .background(
                        Capsule().fill(isActive ? AnyShapeStyle(LinearGradient.cheese)
                                                : AnyShapeStyle(Color.white.opacity(0.06)))
                    )
                    .overlay(
                        // Bradar when the bottle row has focus we ring the active chip white
                        Capsule().stroke(.white.opacity((isActive && zone == .bottles) ? 0.9 : 0), lineWidth: 2)
                    )
                    .shadow(color: isActive ? Color.cheese.opacity(0.4) : .clear, radius: 10)
                    .contentShape(Capsule())
                    .onTapGesture { if !isActive { selected = 0; backend.selectBottle(b.path); playMove() } }
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 6)
    }

    // MARK: cover carousel

    private var coverStrip: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 22) {
                    ForEach(Array(games.enumerated()), id: \.element.id) { idx, g in
                        coverTile(g, isFocused: idx == selected && zone == .games)
                            .id(idx)
                            .onTapGesture {
                                zone = .games
                                if selected == idx { launch() } else { selected = idx; playMove() }
                            }
                    }
                }
                .padding(.horizontal, 60)
                .padding(.vertical, 22)     // Bradar room so the focused tiles gold glow dont clip
            }
            .onChange(of: selected) { _, n in
                withAnimation(.spring(response: 0.3, dampingFraction: 0.82)) { proxy.scrollTo(n, anchor: .center) }
            }
            .onAppear { proxy.scrollTo(selected, anchor: .center) }
        }
        .frame(height: 336)
    }

    private func coverTile(_ g: Game, isFocused: Bool) -> some View {
        ZStack {
            RoundedRectangle(cornerRadius: 14).fill(Color.white.opacity(0.05))
            if let img = covers[g.appid] {
                Image(nsImage: img).resizable().aspectRatio(contentMode: .fill)
            } else {
                VStack(spacing: 10) {
                    Image(systemName: "gamecontroller.fill").font(.system(size: 32))
                    Text(g.name).font(.caption).lineLimit(2).multilineTextAlignment(.center).padding(.horizontal, 8)
                }
                .foregroundStyle(.white.opacity(0.5))
            }
        }
        .frame(width: isFocused ? 202 : 150, height: isFocused ? 288 : 214)
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(isFocused ? Color.cheese : Color.white.opacity(0.06), lineWidth: isFocused ? 4 : 1)
        )
        // Bradar focused tile pops w/ a gold glow, unfocused ones dim + desaturate back
        .shadow(color: isFocused ? Color.cheese.opacity(0.55) : .black.opacity(0.55),
                radius: isFocused ? 26 : 10, y: isFocused ? 2 : 6)
        .saturation(isFocused ? 1.0 : 0.85)
        .opacity(isFocused ? 1.0 : 0.6)
        .scaleEffect(isFocused ? 1.0 : 0.9)
        .animation(.spring(response: 0.3, dampingFraction: 0.8), value: isFocused)
    }

    // MARK: selected-game showcase (logo art + play + meta)

    // Bradar the games real logo art (transparent logo.png) reads WAY more premium than a
    // plain text title -- falls back to a big bold title when a game has no logo art
    private var gameLogo: some View {
        Group {
            if let g = selectedGame, let img = logos[g.appid] {
                Image(nsImage: img).resizable().scaledToFit()
                    .frame(maxWidth: 560, maxHeight: 128, alignment: .bottomLeading)
                    .shadow(color: .black.opacity(0.6), radius: 12, y: 4)
                    .id("logo-\(g.appid)")
                    .transition(.opacity)
            } else {
                Text(selectedGame?.name ?? "")
                    .font(.system(size: 46, weight: .heavy))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .shadow(color: .black.opacity(0.75), radius: 6, y: 2)
                    .id("title-\(selectedGame?.appid ?? "")")
                    .transition(.opacity)
            }
        }
        .frame(maxWidth: 620, minHeight: 128, maxHeight: 128, alignment: .bottomLeading)
        .animation(.easeInOut(duration: 0.3), value: selectedGame?.appid)
    }

    private var metaRow: some View {
        HStack(spacing: 12) {
            Text("\(games.isEmpty ? 0 : selected + 1) / \(games.count)")
                .font(.system(size: 14, weight: .bold, design: .rounded))
                .foregroundStyle(.white.opacity(0.7)).monospacedDigit()
            if let bl = backendLabel {
                Text(bl)
                    .font(.system(size: 11.5, weight: .bold)).kerning(0.5)
                    .foregroundStyle(Color.cheese)
                    .padding(.horizontal, 10).padding(.vertical, 4)
                    .background(Capsule().stroke(Color.cheese.opacity(0.55), lineWidth: 1.2))
            }
        }
    }

    private var playButton: some View {
        HStack(spacing: 10) {
            Image(systemName: launching ? "hourglass" : "play.fill").font(.system(size: 17, weight: .heavy))
            Text(launching ? L("Launching…") : L("Play")).font(.system(size: 18, weight: .heavy)).kerning(0.5)
        }
        .foregroundStyle(Color.onCheese)
        .padding(.horizontal, 30).padding(.vertical, 15)
        .background(Capsule().fill(LinearGradient.cheese))
        .overlay(Capsule().stroke(.white.opacity(zone == .games ? 0.85 : 0), lineWidth: 2))
        .shadow(color: Color.cheese.opacity(zone == .games ? 0.6 : 0.25),
                radius: zone == .games ? 22 : 10, y: 4)
        .scaleEffect((zone == .games && pulse) ? 1.035 : 1.0)
        .animation(.easeInOut(duration: 0.15), value: launching)
        .contentShape(Capsule())
        .onTapGesture { launch() }
    }

    // Bradar the bottles global backend, shown as a lil badge (auto -> no badge)
    private var backendLabel: String? {
        switch (activeBottle?.defaultBackend ?? "auto").lowercased() {
        case "dxvk": return "DXVK"
        case "dxmt": return "DXMT"
        case "d3dmetal", "gptk", "gptk_full": return "D3DMETAL"
        case "vr", "dxmt_openxr": return "VR"
        default: return nil
        }
    }

    private var emptyStrip: some View {
        VStack(spacing: 14) {
            Image(systemName: "tray").font(.system(size: 50)).foregroundStyle(.white.opacity(0.35))
            Text(L("No games in this bottle")).font(.title2.bold()).foregroundStyle(.white.opacity(0.75))
            Text(L("Press ▲ to switch bottle, B to exit")).font(.callout).foregroundStyle(.white.opacity(0.45))
        }
        .frame(maxWidth: .infinity)
    }

    // MARK: bottom controller-hint bar

    private var bottomBar: some View {
        HStack(spacing: 22) {
            HStack(spacing: 8) {
                Image(systemName: "line.3.horizontal").font(.system(size: 14, weight: .bold))
                Text(L("MENU")).font(.system(size: 12.5, weight: .bold)).kerning(1)
            }
            .foregroundStyle(.white.opacity(0.55))
            Spacer()
            if bottles.count > 1 { hint(glyph: "arrowtriangle.up.fill", tint: .white.opacity(0.75), label: L("Bottle")) }
            hint(glyph: "arrow.left.and.right", tint: .white.opacity(0.75), label: L("Browse"))
            hint(glyph: "a.circle.fill", tint: Color.cheese, label: L("Play"))
            hint(glyph: "b.circle.fill", tint: Color.wine, label: L("Back"))
        }
        .padding(.horizontal, 40)
        .padding(.vertical, 15)
        .background(LinearGradient(colors: [.clear, .black.opacity(0.6)], startPoint: .top, endPoint: .bottom))
        .overlay(Rectangle().fill(.white.opacity(0.06)).frame(height: 1), alignment: .top)
    }

    private func hint(glyph: String, tint: Color, label: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: glyph).font(.system(size: 16)).foregroundStyle(tint)
            Text(label).font(.system(size: 13, weight: .semibold)).foregroundStyle(.white.opacity(0.85))
        }
    }

    // MARK: navigation (zone-aware)

    private func goLeft()  { zone == .games ? move(-1) : switchBottle(-1) }
    private func goRight() { zone == .games ? move(1)  : switchBottle(1) }
    private func goUp() {
        if zone == .games, !bottles.isEmpty { zone = .bottles; playMove() }
    }
    private func goDown() {
        if zone == .bottles { zone = .games; playMove() }
    }
    private func doSelect() {
        switch zone {
        case .games:   launch()
        case .bottles: zone = .games; playSelect()   // Bradar confirm the bottle, drop to the games
        }
    }

    private func move(_ delta: Int) {
        let n = selected + delta
        guard n >= 0, n < games.count else { playEdge(); return }
        selected = n
        playMove()
    }

    private func switchBottle(_ delta: Int) {
        guard !bottles.isEmpty else { return }
        let cur = bottles.firstIndex { $0.path == backend.activePrefix } ?? 0
        let n = cur + delta
        guard n >= 0, n < bottles.count else { playEdge(); return }
        playMove()
        selected = 0
        zone = .bottles
        backend.selectBottle(bottles[n].path)   // Bradar backend.games updates -> strip + hero reload
    }

    private func launch() {
        guard !launching, let prefix = backend.activePrefix, let g = selectedGame else { return }
        playSelect()
        launching = true
        Task {
            // Bradar pull the SAME saved per-game config the grid/detail launch uses so the
            // game runs w/ its normal settings (backend, args, retina, sync, env, steam mode)
            let cfg = await backend.getGameConfig(prefix: prefix, appid: g.appid)
            let exe = (cfg["exe"] as? String ?? "").isEmpty ? (g.exe ?? "") : (cfg["exe"] as? String ?? "")
            guard !exe.isEmpty else { launching = false; return }
            let esync = cfg["esync"] as? Bool ?? true
            let msync = cfg["msync"] as? Bool ?? true
            await backend.launchGame(
                prefix: prefix, exe: exe, args: cfg["args"] as? String ?? "",
                backend: cfg["backend"] as? String ?? "auto", installDir: g.installDir,
                retinaMode: cfg["retina_mode"] as? Bool ?? (NSScreen.main.map { $0.backingScaleFactor > 1.0 } ?? false),
                metalHud: cfg["metal_hud"] as? Bool ?? false,
                esync: msync ? false : esync, msync: msync,
                // Bradar the steam appID/mode/name make steamworks games launch like normal
                // (missin these = "[API loaded no]"), same as the grid/detail path does it
                gameName: g.name,
                steamAppId: g.appid,
                steamMode: cfg["steam_mode"] as? String ?? "silent",
                customEnv: cfg["custom_env"] as? String ?? "",
                debug: cfg["debug"] as? Bool ?? false)
            launching = false
            // Bradar drop back to the desktop so the games own window is visible (a wine
            // window openin behind our fullscreen Space would look like nothin happend)
            exitFullscreen()
            isPresented = false
        }
    }

    private func exitConsole() { playBack(); exitFullscreen(); isPresented = false }

    // MARK: native fullscreen

    private func consoleWinodw() -> NSWindow? {
        NSApp.keyWindow ?? NSApp.mainWindow ?? NSApp.windows.first(where: { $0.isVisible })
    }
    private func entrFullscreen() {
        guard let win = consoleWinodw() else { return }
        if !win.styleMask.contains(.fullScreen) {
            didEntrFullscreen = true      // Bradar remember WE did it so we only undo our own
            win.toggleFullScreen(nil)
        }
    }
    private func exitFullscreen() {
        guard didEntrFullscreen, let win = consoleWinodw() else { return }
        if win.styleMask.contains(.fullScreen) { win.toggleFullScreen(nil) }
        didEntrFullscreen = false
    }

    // MARK: gamepad wiring

    private func wireTheGamepad() {
        pad.onMuve   = { d in d < 0 ? goLeft() : goRight() }
        pad.onVert   = { d in d < 0 ? goUp()   : goDown() }
        pad.onBottle = { d in switchBottle(d) }         // Bradar shoulders swap bottle anytime
        pad.onSelct  = { doSelect() }
        pad.onBak    = { exitConsole() }
        pad.start()
    }

    // Bradar snappy nav sounds via CoreAudio system-sound IDs (pre-registerd once, so no
    // per-play load lag like NSSound(named:) had -> fixes the "sounds a bit behind" delay)
    private func playMove()   { ConsoleSonds.shared.play("Tink") }
    private func playSelect() { ConsoleSonds.shared.play("Glass") }
    private func playEdge()   { ConsoleSonds.shared.play("Funk") }
    private func playOpen()   { ConsoleSonds.shared.play("Hero") }
    private func playBack()   { ConsoleSonds.shared.play("Bottle") }

    // MARK: art loading

    private func loadCovers() {
        for g in games where covers[g.appid] == nil {
            guard let s = g.coverUrl, let url = URL(string: s) else { continue }
            Task.detached(priority: .utility) {
                if let (data, _) = try? await URLSession.shared.data(from: url),
                   let img = NSImage(data: data) {
                    await MainActor.run { covers[g.appid] = img }
                }
            }
        }
    }

    // Bradar the cinematic backdrop = Steam's landscape library_hero (falls back to header,
    // then to the portrait cover) -- only for numeric steam appids; epic/manual just get the bg
    private func loadHero() {
        guard let g = selectedGame, heroes[g.appid] == nil, Int(g.appid) != nil else { return }
        let appid = g.appid
        let cover = g.coverUrl
        Task.detached(priority: .utility) {
            let tries = [
                "https://steamcdn-a.akamaihd.net/steam/apps/\(appid)/library_hero.jpg",
                "https://cdn.cloudflare.steamstatic.com/steam/apps/\(appid)/library_hero.jpg",
                "https://steamcdn-a.akamaihd.net/steam/apps/\(appid)/header.jpg",
            ] + (cover.map { [$0] } ?? [])
            for s in tries {
                if let url = URL(string: s),
                   let (data, _) = try? await URLSession.shared.data(from: url),
                   let img = NSImage(data: data) {
                    await MainActor.run { heroes[appid] = img }
                    return
                }
            }
        }
    }

    // Bradar the games transparent logo art -- makes the showcase look pro (steam appids only)
    private func loadLogo() {
        guard let g = selectedGame, logos[g.appid] == nil, Int(g.appid) != nil else { return }
        let appid = g.appid
        Task.detached(priority: .utility) {
            let tries = [
                "https://steamcdn-a.akamaihd.net/steam/apps/\(appid)/logo.png",
                "https://cdn.cloudflare.steamstatic.com/steam/apps/\(appid)/logo.png",
            ]
            for s in tries {
                if let url = URL(string: s),
                   let (data, _) = try? await URLSession.shared.data(from: url),
                   let img = NSImage(data: data) {
                    await MainActor.run { logos[appid] = img }
                    return
                }
            }
        }
    }
}

// MARK: - low-latency sound board

/// Bradar registers the macOS system-sound .aiff files ONCE as CoreAudio SystemSoundIDs so
/// each play is fire-n-forget w/ basicly zero latency (overlappin plays are fine too).
final class ConsoleSonds {
    static let shared = ConsoleSonds()
    private var ids: [String: SystemSoundID] = [:]
    private init() { for n in ["Tink", "Glass", "Funk", "Hero", "Bottle"] { registr(n) } }
    private func registr(_ name: String) {
        let url = URL(fileURLWithPath: "/System/Library/Sounds/\(name).aiff")
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        var sid: SystemSoundID = 0
        if AudioServicesCreateSystemSoundID(url as CFURL, &sid) == noErr { ids[name] = sid }
    }
    func play(_ name: String) { if let sid = ids[name] { AudioServicesPlaySystemSound(sid) } }
}

// MARK: - gamepad reader

/// Bradar bridges GameController input into the console. hands each event to a closure the
/// view sets. d-pad + left stick move (horizontal) / change zone (vertical), A plays, B exits,
/// the shoulder buttons swap bottle. stick is throttled so a held direction dont fly past.
final class GamepadRaeder: ObservableObject {
    var onMuve: (Int) -> Void = { _ in }     // horizontal: -1 left / +1 right
    var onVert: (Int) -> Void = { _ in }     // vertical:   -1 up   / +1 down
    var onBottle: (Int) -> Void = { _ in }   // shoulders:  -1 prev / +1 next bottle
    var onSelct: () -> Void = {}
    var onBak: () -> Void = {}

    private var connctObserver: NSObjectProtocol?
    private var lastAxisMuve = Date.distantPast

    func start() {
        for c in GCController.controllers() { attch(c) }
        connctObserver = NotificationCenter.default.addObserver(
            forName: .GCControllerDidConnect, object: nil, queue: .main) { [weak self] note in
            if let c = note.object as? GCController { self?.attch(c) }
        }
        GCController.startWirelessControllerDiscovery(completionHandler: {})
    }

    func stop() {
        if let o = connctObserver { NotificationCenter.default.removeObserver(o) }
        connctObserver = nil
    }

    private func attch(_ c: GCController) {
        guard let gp = c.extendedGamepad else { return }
        // Bradar d-pad: one step per press (pressed==true edge)
        gp.dpad.left.pressedChangedHandler  = { [weak self] _, _, p in if p { DispatchQueue.main.async { self?.onMuve(-1) } } }
        gp.dpad.right.pressedChangedHandler = { [weak self] _, _, p in if p { DispatchQueue.main.async { self?.onMuve(1)  } } }
        gp.dpad.up.pressedChangedHandler    = { [weak self] _, _, p in if p { DispatchQueue.main.async { self?.onVert(-1) } } }
        gp.dpad.down.pressedChangedHandler  = { [weak self] _, _, p in if p { DispatchQueue.main.async { self?.onVert(1)  } } }
        // Bradar A = play/confirm, B = back
        gp.buttonA.pressedChangedHandler = { [weak self] _, _, p in if p { DispatchQueue.main.async { self?.onSelct() } } }
        gp.buttonB.pressedChangedHandler = { [weak self] _, _, p in if p { DispatchQueue.main.async { self?.onBak()   } } }
        // Bradar shoulder bumpers swap the bottle (L1 prev / R1 next) like Steam Deck
        gp.leftShoulder.pressedChangedHandler  = { [weak self] _, _, p in if p { DispatchQueue.main.async { self?.onBottle(-1) } } }
        gp.rightShoulder.pressedChangedHandler = { [weak self] _, _, p in if p { DispatchQueue.main.async { self?.onBottle(1)  } } }
        // Bradar left stick: throttled so holdin it steps one at a time, not a blur
        gp.leftThumbstick.valueChangedHandler = { [weak self] _, x, y in
            guard let self else { return }
            guard abs(x) > 0.6 || abs(y) > 0.6 else { return }
            let now = Date()
            guard now.timeIntervalSince(self.lastAxisMuve) > 0.22 else { return }
            self.lastAxisMuve = now
            if abs(x) > abs(y) {
                DispatchQueue.main.async { self.onMuve(x > 0 ? 1 : -1) }
            } else {
                // up on the stick = up (toward the bottle row); down = back to games
                DispatchQueue.main.async { self.onVert(y > 0 ? -1 : 1) }
            }
        }
    }
}
