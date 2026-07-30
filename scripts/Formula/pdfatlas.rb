class Pdfatlas < Formula
  include Language::Python::Virtualenv

  desc "PDF Reader with Search Portals and Auto-Crop"
  homepage "https://github.com/aziis98/pdfatlas"
  url "https://github.com/aziis98/pdfatlas/archive/refs/heads/main.tar.gz"
  version "0.1.0"
  license "AGPL-3.0-only"

  depends_on "cmake" => :build
  depends_on "pkg-config" => :build
  depends_on "cairo"
  depends_on "gtk4"
  depends_on "libadwaita"
  depends_on "pygobject3"
  depends_on "python@3.12"

  def install
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install_and_link buildpath
  end

  test do
    system "#{bin}/pdfatlas", "--help"
  end
end
