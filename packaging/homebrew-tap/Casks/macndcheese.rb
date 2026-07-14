cask "macndcheese" do
  version "10.5.0"
  sha256 "d022764c20d684941c863c455eaa2729249aa496b548424962391306aa77bcbb"

  url "https://github.com/mont127/MacNdCheese/releases/download/v#{version}/MacNCheese.dmg"
  name "MacNdCheese"
  desc "Launcher for running Windows games via Wine, DXVK, MoltenVK and D3DMetal"
  homepage "https://github.com/mont127/MacNdCheese"

  livecheck do
    url :url
    strategy :github_latest
  end

  depends_on macos: :sonoma

  app "MacNdCheese Launcher.app"

  # ad-hoc signed only (no Developer ID, not notarized) — clear the quarantine
  # flag Gatekeeper adds on download so it doesn't need "Open Anyway" by hand.
  postflight do
    system_command "/usr/bin/xattr", args: ["-dr", "com.apple.quarantine", "#{appdir}/MacNdCheese Launcher.app"]
  end

  zap trash: [
    "~/Library/Application Support/MacNdCheese",
    "~/Library/Preferences/com.marcel.macncheese.plist",
  ]

  caveats <<~EOS
    MacNdCheese is ad-hoc signed, not notarized by Apple. This cask clears the
    quarantine flag on install so it should open normally. If macOS still says
    it "can't be verified": System Settings → Privacy & Security → scroll to
    the blocked-app message → "Open Anyway".
  EOS
end
