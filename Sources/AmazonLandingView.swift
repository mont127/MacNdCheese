import AppKit
import SwiftUI
import WebKit

struct AmazonLandingView: View {
    @EnvironmentObject var backend: BackendClient
    @Binding var searchText: String

    enum Phase { case downloading, auth, library }

    @State private var phase: Phase = .downloading
    @State private var pollTimer: Timer?

    var body: some View {
        Group {
            switch phase {
            case .downloading:
                AmazonDownloadingView()
            case .auth:
                AmazonAuthView(onAuthenticated: { transitionToLibrary() })
            case .library:
                // Library browsing/installing lands in a follow-up phase; for now
                // confirm the connected account so users know sign-in succeeded.
                AmazonComingSoonView(displayName: backend.amazonDisplayName)
            }
        }
        .animation(.easeInOut(duration: 0.22), value: phase == .library)
        .onAppear { onAppearHandler() }
        .onDisappear { stopDownloadPolling() }
        .onChange(of: backend.nileInstalled) { _, installed in
            if installed && phase == .downloading { transitionToAuth() }
        }
    }

    private func onAppearHandler() {
        if backend.nileInstalled {
            // Always do a fresh auth check — the cached value belongs to the previous bottle.
            phase = .auth
            Task {
                await backend.amazonCheckAuth()
                if backend.amazonAuthenticated { transitionToLibrary() }
            }
            return
        }
        // Nile not yet downloaded — poll until it is.
        phase = .downloading
        startDownloadPolling()
        Task {
            await backend.nileStatus()
            if backend.nileInstalled {
                stopDownloadPolling()
                await backend.amazonCheckAuth()
                if backend.amazonAuthenticated { transitionToLibrary() } else { phase = .auth }
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
    }

    /// Polls nile_status every 2 s while Nile is being downloaded.
    private func startDownloadPolling() {
        stopDownloadPolling()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { _ in
            Task { @MainActor in
                await backend.nileStatus()
                if backend.nileInstalled {
                    stopDownloadPolling()
                    await backend.amazonCheckAuth()
                    if backend.amazonAuthenticated { transitionToLibrary() } else { phase = .auth }
                }
            }
        }
    }

    private func stopDownloadPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
    }
}

// MARK: - Downloading state

private struct AmazonDownloadingView: View {
    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            AmazonLogo(size: 80)
                .padding(.bottom, 12)
            Text(L("AMAZON GAMES"))
                .font(.system(.largeTitle, design: .default).weight(.bold))
                .tracking(4)
            Spacer().frame(height: 32)
            ProgressView()
                .controlSize(.large)
                .padding(.bottom, 12)
            Text(L("Preparing Amazon Games support…"))
                .foregroundStyle(.secondary)
                .font(.subheadline)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

// MARK: - Auth state

struct AmazonAuthView: View {
    @EnvironmentObject var backend: BackendClient
    var onAuthenticated: () -> Void

    @State private var showWebView = false
    @State private var isAuthenticating = false
    @State private var isPreparing = false
    @State private var errorMessage: String? = nil

    // Single-attempt PKCE state fetched fresh from the backend right before
    // presenting the sign-in sheet, then echoed back unchanged on success.
    @State private var pendingURL: URL? = nil
    @State private var pendingClientId = ""
    @State private var pendingCodeVerifier = ""
    @State private var pendingSerial = ""

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            AmazonLogo(size: 80)
                .padding(.bottom, 8)

            Text(L("AMAZON GAMES"))
                .font(.system(.largeTitle, design: .default).weight(.bold))
                .tracking(4)

            Text(L("Connect your Amazon account to access your game library."))
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.top, 6)
                .padding(.horizontal, 40)

            Spacer().frame(height: 32)

            Button {
                startSignIn()
            } label: {
                HStack(spacing: 8) {
                    if isAuthenticating || isPreparing {
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
            .tint(.orange)
            .controlSize(.large)
            .disabled(isAuthenticating || isPreparing)

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
            if let url = pendingURL {
                AmazonWebAuthSheet(loginURL: url, isPresented: $showWebView) { code in
                    authenticate(code: code)
                }
            }
        }
    }

    private func startSignIn() {
        errorMessage = nil
        isPreparing = true
        Task {
            let params = await backend.nileGetAuthParams()
            isPreparing = false
            guard let url = params.url else {
                errorMessage = L("Couldn't start Amazon sign-in. Please try again.")
                return
            }
            pendingURL = url
            pendingClientId = params.clientId
            pendingCodeVerifier = params.codeVerifier
            pendingSerial = params.serial
            showWebView = true
        }
    }

    private func authenticate(code: String) {
        isAuthenticating = true
        errorMessage = nil
        Task {
            let result = await backend.amazonAuth(
                code: code, clientId: pendingClientId,
                codeVerifier: pendingCodeVerifier, serial: pendingSerial
            )
            isAuthenticating = false
            if result.ok {
                await backend.amazonCheckAuth()
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

struct AmazonWebAuthSheet: View {
    let loginURL: URL
    @Binding var isPresented: Bool
    var onCodeCaptured: (String) -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(L("Sign in to Amazon"))
                    .font(.headline)
                Spacer()
                Button(L("Cancel")) { isPresented = false }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(.bar)

            Divider()

            AmazonWebView(loginURL: loginURL) { code in
                isPresented = false
                onCodeCaptured(code)
            }
        }
        .frame(width: 860, height: 640)
    }
}

// MARK: - WKWebView wrapper

private struct AmazonWebView: NSViewRepresentable {
    let loginURL: URL
    var onCodeCaptured: (String) -> Void

    func makeNSView(context: Context) -> WKWebView {
        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences = prefs
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
                  let url = webView.url,
                  let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
                  let items = components.queryItems,
                  let code = items.first(where: { $0.name == "openid.oa2.authorization_code" })?.value,
                  !code.isEmpty
            else { return }

            captured = true
            onCodeCaptured(code)
        }
    }
}

// MARK: - Post-auth placeholder (library browsing lands separately)

private struct AmazonComingSoonView: View {
    let displayName: String?

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            AmazonLogo(size: 64)
                .padding(.bottom, 12)
            Text(L("Connected"))
                .font(.title2.weight(.bold))
            if let name = displayName, !name.isEmpty {
                Text(String(format: L("Signed in as %@"), name))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .padding(.top, 4)
            }
            Text(L("Your Amazon Games library is coming soon."))
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .padding(.top, 12)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
