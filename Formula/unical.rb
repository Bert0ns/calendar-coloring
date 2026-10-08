# Homebrew Formula for unical
class Unical < Formula
  desc "Sync a read-only university calendar into a color-coded Google Calendar"
  homepage "https://github.com/Bert0ns/uni-calendar-coloring"
  version "1.0.1"
  license "MIT"

  on_macos do
    if Hardware::CPU.arm?
      url "https://github.com/Bert0ns/uni-calendar-coloring/releases/download/v#{version}/unical-macos-arm64.tar.gz"
      sha256 "8c954245cd97a72dd6e54e93d07fb33777bacf6ccbacf8005ff315ede136d0b9"
    else
      url "https://github.com/Bert0ns/uni-calendar-coloring/releases/download/v#{version}/unical-macos-x86_64.tar.gz"
      sha256 "8c954245cd97a72dd6e54e93d07fb33777bacf6ccbacf8005ff315ede136d0b9"
    end
  end

  on_linux do
    url "https://github.com/Bert0ns/uni-calendar-coloring/releases/download/v#{version}/unical-linux-x86_64.tar.gz"
    sha256 "d4c9e81ae2766da95aa4026036be59c0de366a2e44b1a25076aa64c3ae134872"
  end

  def install
    bin.install "unical"
  end

  test do
    assert_match "Sync and color Google Calendar events", shell_output("#{bin}/unical --help")
  end
end
