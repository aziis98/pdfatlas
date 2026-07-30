class Pdfatlas < Formula
  include Language::Python::Virtualenv

  desc "PDF Reader with Search Portals and Auto-Crop"
  homepage "https://github.com/aziis98/pdfatlas"
  url "https://github.com/aziis98/pdfatlas/archive/refs/heads/main.tar.gz"
  version "0.1.0"
  license "AGPL-3.0-only"

  depends_on "cairo"
  depends_on "cmake" => :build
  depends_on "gtk4"
  depends_on "libadwaita"
  depends_on "pkg-config" => :build
  depends_on "pygobject3"
  depends_on "python@3.14"

  def install
    system "python3.14", "-m", "venv", libexec
    system libexec/"bin/python", "-m", "pip", "install", "--upgrade", "pip"
    system libexec/"bin/python", "-m", "pip", "install", *std_pip_args(build_isolation: true), "."

    bin.install_symlink libexec/"bin/pdfatlas"
  end

  test do
    system "#{bin}/pdfatlas", "--help"
  end
end
