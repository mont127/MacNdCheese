cask "macndcheese" do
  version "10.4.1"
  sha256 "93b4785f9a7552c8dcc59e4efc4ef73447740925c56a60882fe68f649086402c"

  url "https://github.com/mont127/MacNdCheese/releases/download/v#{version}/MacNCheese.dmg"
  name "MacNdCheese"
  desc "Launcher for running Windows games on macOS via Wine, DXVK, MoltenVK and D3DMetal"
  homepage "https://github.com/mont127/MacNdCheese"

  livecheck do
    url :url
    strategy :github_latest
  end

  depends_on macos: :monterey

  app "MacNdCheese Launcher.app"

  # The .app is only ad-hoc signed (no paid Apple Developer ID, no notarization),
  # so without this Gatekeeper would quarantine it just like a manual .dmg
  # install and show "can't be verified" on first launch.
  postflight do
    system_command "/usr/bin/xattr",
                    args: ["-dr", "com.apple.quarantine", "#{appdir}/MacNdCheese Launcher.app"]
  end

  caveats <<~EOS
    MacNdCheese is ad-hoc signed, not notarized by Apple. This cask clears the
    quarantine flag on install so it should open normally. If macOS still says
    it "can't be verified": System Settings → Privacy & Security → scroll to
    the blocked-app message → "Open Anyway".
  EOS

  zap trash: [
    "~/Library/Application Support/MacNdCheese",
    "~/Library/Preferences/com.marcel.macncheese.plist",
  ]
end
