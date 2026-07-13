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

  zap trash: [
    "~/Library/Application Support/MacNdCheese",
    "~/Library/Preferences/com.marcel.macncheese.plist",
  ]

  caveats <<~EOS
    MacNdCheese is ad-hoc signed, not notarized by Apple. The first time you
    open it macOS will say it "can't be verified" or is damaged. To run it:
      1. System Settings → Privacy & Security
      2. Scroll to the blocked-app message for "MacNdCheese Launcher"
      3. Click "Open Anyway", then confirm with your password
  EOS
end
