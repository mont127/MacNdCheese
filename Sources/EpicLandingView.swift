import AppKit
import SwiftUI
import WebKit

struct EpicLandingView: View {
    @EnvironmentObject var backend: BackendClient
    @Binding var searchText: String

    enum Phase { case downloading, checkingAuth, auth, library }

    @State private var phase: Phase = .downloading
    @State private var pollTimer: Timer?
    @State private var gamesPollTask: Task<Void, Never>? = nil
    @State private var isFetchingGames = true
    /// The bottle path this instance is polling for. `backend.games` is a
    /// single array shared with Steam/manual bottles; once the user switches
    /// away, `selectBottle` clears it for the NEW bottle while this view may
    /// still be live mid-fade-out (SwiftUI keeps `.transition(.opacity)`
    /// views reactive during their removal animation). Without this guard,
    /// that clear briefly makes EpicLibraryView think there are zero games
    /// and flash its own empty state right as it's fading away.
    @State private var ownPrefix: String?

    private var isShowingOwnBottle: Bool {
        guard let ownPrefix else { return true }
        return backend.activePrefix == ownPrefix
    }

    var body: some View {
        Group {
            switch phase {
            case .downloading:
                EpicDownloadingView()
            case .checkingAuth:
                EpicDownloadingView(message: L("Checking your Epic Games account…"))
            case .auth:
                EpicAuthView(onAuthenticated: { transitionToLibrary() })
            case .library:
                EpicLibraryView(games: sortedGames, searchText: $searchText, isFetching: isFetchingGames || !isShowingOwnBottle)
            }
        }
        .animation(.easeInOut(duration: 0.22), value: phase == .library)
        .onAppear { onAppearHandler() }
        .onDisappear { stopAll() }
        .onChange(of: backend.legendaryInstalled) { installed in
            if installed && phase == .downloading { transitionToAuth() }
        }
    }

    private var sortedGames: [Game] {
        guard isShowingOwnBottle else { return [] }
        return backend.games.sorted {
            ($0.isInstalled ? 0 : 1) < ($1.isInstalled ? 0 : 1)
        }
    }

    private func onAppearHandler() {
        // Set this up front, not just inside startGamesPolling() — it also
        // needs to gate the auth-check phase below, so a bottle switch that
        // happens mid-check doesn't let a stale response flip phase/games
        // for a view that's no longer showing this bottle.
        ownPrefix = backend.activePrefix
        if backend.legendaryInstalled {
            // Always do a fresh auth check — the cached value belongs to the
            // previous bottle — but show a loading state while it's in
            // flight instead of the "connect your account" prompt: jumping
            // straight to .auth was misleading whenever the user was
            // already signed in, which is the common case.
            phase = .checkingAuth
            Task {
                await backend.epicCheckAuth()
                guard isShowingOwnBottle else { return }
                if backend.epicAuthenticated { transitionToLibrary() } else { phase = .auth }
            }
            return
        }
        // Legendary not yet downloaded — poll until it is.
        phase = .downloading
        startDownloadPolling()
        Task {
            await backend.legendaryStatus()
            guard isShowingOwnBottle else { return }
            if backend.legendaryInstalled {
                stopDownloadPolling()
                phase = .checkingAuth
                await backend.epicCheckAuth()
                guard isShowingOwnBottle else { return }
                if backend.epicAuthenticated { transitionToLibrary() } else { phase = .auth }
            }
        }
    }

    private func transitionToAuth() {
        stopDownloadPolling()
        phase = .auth
    }

    private func transitionToLibrary() {
        stopDownloadPolling()
        phase = .library
        startGamesPolling()
    }

    /// Polls legendary_status every 2 s while legendary is being downloaded.
    private func startDownloadPolling() {
        stopDownloadPolling()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in
            Task { @MainActor in
                await backend.legendaryStatus()
                if backend.legendaryInstalled {
                    stopDownloadPolling()
                    await backend.epicCheckAuth()
                    if backend.epicAuthenticated { transitionToLibrary() } else { phase = .auth }
                }
            }
        }
    }

    private func stopDownloadPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    /// Polls scan_games and download state every 3 s.
    private func startGamesPolling() {
        gamesPollTask?.cancel()
        isFetchingGames = true
        guard let prefix = backend.activePrefix else { return }
        ownPrefix = prefix
        gamesPollTask = Task {
            await backend.scanGames(prefix: prefix)
            await backend.refreshEpicDownloads()
            isFetchingGames = false
            // Poll until games appear (cold start / fresh install)
            var attempts = 0
            while !Task.isCancelled && backend.games.isEmpty && attempts < 100 {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                guard !Task.isCancelled, backend.activePrefix == prefix else { break }
                await backend.scanGames(prefix: prefix)
                await backend.refreshEpicDownloads()
                attempts += 1
            }
            // Keep polling downloads; refresh library only when a download is active.
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                guard !Task.isCancelled, backend.activePrefix == prefix else { break }
                await backend.refreshEpicDownloads()
                if !backend.epicDownloads.isEmpty {
                    await backend.scanGames(prefix: prefix)
                }
            }
        }
    }

    private func stopAll() {
        stopDownloadPolling()
        gamesPollTask?.cancel()
        gamesPollTask = nil
    }
}

// MARK: - Downloading / checking state

private struct EpicDownloadingView: View {
    var message: String = L("Preparing Epic Games support…")

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            EpicLogo(size: 80)
                .padding(.bottom, 12)
            Text(L("EPIC GAMES"))
                .font(.system(.largeTitle, design: .default).weight(.bold))
                .tracking(4)
            Spacer().frame(height: 32)
            ProgressView()
                .controlSize(.large)
                .padding(.bottom, 12)
            Text(message)
                .foregroundStyle(.secondary)
                .font(.subheadline)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Auth state

struct EpicAuthView: View {
    @EnvironmentObject var backend: BackendClient
    var onAuthenticated: () -> Void

    @State private var showWebView = false
    @State private var isAuthenticating = false
    @State private var errorMessage: String? = nil

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            EpicLogo(size: 80)
                .padding(.bottom, 8)

            Text(L("EPIC GAMES"))
                .font(.system(.largeTitle, design: .default).weight(.bold))
                .tracking(4)

            Text(L("Connect your Epic Games account to access your library."))
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.top, 6)
                .padding(.horizontal, 40)

            Spacer().frame(height: 32)

            Button {
                showWebView = true
            } label: {
                HStack(spacing: 8) {
                    if isAuthenticating {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "person.badge.key")
                    }
                    Text(isAuthenticating ? L("Signing in…") : L("Connect"))
                        .fontWeight(.bold)
                }
                .frame(width: 160, height: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(.indigo)
            .controlSize(.large)
            .disabled(isAuthenticating)

            if let err = errorMessage {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
                    .padding(.top, 12)
                    .padding(.horizontal, 40)
            }

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .sheet(isPresented: $showWebView) {
            if let url = backend.epicAuthURL {
                EpicWebAuthSheet(loginURL: url, isPresented: $showWebView) { code in
                    authenticate(code: code)
                }
            }
        }
    }

    private func authenticate(code: String) {
        isAuthenticating = true
        errorMessage = nil
        Task {
            let result = await backend.epicAuth(code: code)
            isAuthenticating = false
            if result.ok {
                await backend.epicCheckAuth()
                onAuthenticated()
            } else {
                errorMessage = result.error.isEmpty
                    ? L("Authentication failed. Please try again.")
                    : result.error
            }
        }
    }
}

// MARK: - In-app WebKit browser

struct EpicWebAuthSheet: View {
    let loginURL: URL
    @Binding var isPresented: Bool
    var onCodeCaptured: (String) -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(L("Sign in to Epic Games"))
                    .font(.headline)
                Spacer()
                Button(L("Cancel")) { isPresented = false }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(.bar)

            Divider()

            EpicWebView(loginURL: loginURL) { code in
                isPresented = false
                onCodeCaptured(code)
            }
        }
        .frame(width: 860, height: 640)
    }
}

// MARK: - Epic Store sheet

struct EpicStoreSheet: View {
    @Binding var isPresented: Bool

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(L("Epic Games Store"))
                    .font(.headline)
                Spacer()
                Button(L("Done")) { isPresented = false }
                    .keyboardShortcut(.defaultAction)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(.bar)

            Divider()

            if let url = URL(string: "https://store.epicgames.com") {
                EpicStoreWebView(url: url)
            }
        }
        .frame(minWidth: 1024, minHeight: 720)
    }
}

private struct EpicStoreWebView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> WKWebView {
        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences = prefs
        config.websiteDataStore = .nonPersistent()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {}
}

// MARK: - WKWebView wrapper

private struct EpicWebView: NSViewRepresentable {
    let loginURL: URL
    var onCodeCaptured: (String) -> Void

    func makeNSView(context: Context) -> WKWebView {
        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences = prefs
        // Ephemeral store: the default store is shared/persistent across every WKWebView
        // in the app, so a login in one bottle's sign-in sheet would leave a session
        // cookie that silently auto-authed every other bottle's sheet with the same
        // Epic account instead of prompting.
        config.websiteDataStore = .nonPersistent()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.load(URLRequest(url: loginURL))
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {}

    func makeCoordinator() -> Coordinator {
        Coordinator(onCodeCaptured: onCodeCaptured)
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        var onCodeCaptured: (String) -> Void
        private var captured = false

        init(onCodeCaptured: @escaping (String) -> Void) {
            self.onCodeCaptured = onCodeCaptured
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            guard !captured,
                  let url = webView.url?.absoluteString,
                  url.contains("/id/api/redirect") else { return }

            // The redirect page returns JSON — extract the authorizationCode from it.
            webView.evaluateJavaScript("document.body.innerText") { [weak self] result, _ in
                guard let self,
                      !self.captured,
                      let text = result as? String,
                      let data = text.data(using: .utf8),
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                else { return }

                let code = (json["authorizationCode"] as? String) ?? (json["sid"] as? String) ?? ""
                guard !code.isEmpty else { return }
                self.captured = true
                DispatchQueue.main.async { self.onCodeCaptured(code) }
            }
        }
    }
}