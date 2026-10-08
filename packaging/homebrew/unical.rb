# Homebrew Formula for unical
# To distribute via a Homebrew tap (e.g., Bert0ns/homebrew-tap):
# 1. Create a GitHub repo named homebrew-tap (or Bert0ns/tap)
# 2. Place this file in Formula/unical.rb inside the tap repo
# 3. Update the version and sha256 checksums from the latest release
# 4. Users install with: brew install Bert0ns/tap/unical

class Unical < Formula
  desc "Sync a read-only university calendar into a color-coded Google Calendar"
  homepage "https://github.com/Bert0ns/uni-calendar-coloring"
  version "1.0.0"
  license "MIT"

  on_macos do
    if Hardware::CPU.arm?
      url "https://github.com/Bert0ns/uni-calendar-coloring/releases/download/v#{version}/unical-macos-arm64.tar.gz"
      sha256 "ed4f4009ac0a1a099694aa008b8a53776c416621e971ee0ecb738839ff3c3e53"
    else
      url "https://github.com/Bert0ns/uni-calendar-coloring/releases/download/v#{version}/unical-macos-x86_64.tar.gz"
      sha256 "ed4f4009ac0a1a099694aa008b8a53776c416621e971ee0ecb738839ff3c3e53"
    end
  end

  on_linux do
    url "https://github.com/Bert0ns/uni-calendar-coloring/releases/download/v#{version}/unical-linux-x86_64.tar.gz"
    sha256 "d008c620a15270e3f1e492bf2605dd60b4a0b6fa56238cfbb8edc0ca67fe845a"
  end

  def install
    bin.install "unical"
  end

  test do
    assert_match "Sync and color Google Calendar events", shell_output("#{bin}/unical --help")
  end
end
