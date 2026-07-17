#!/usr/bin/env sh
# macOS/Linux counterpart to build_exe.bat. Single source of truth for the
# release build is AllInOneSnapchatDownloader.spec (issue #45); any future
# tweaks belong in the .spec, not in flags here.
set -e

echo "Building Snapchat Memories Downloader..."
pyinstaller --clean AllInOneSnapchatDownloader.spec
echo
echo "Build complete! Check the dist folder."
# PyInstaller builds for the OS it runs on — it cannot cross-compile.
case "$(uname -s)" in
    Darwin) echo "  macOS build: dist/AllInOneSnapchatDownloader.app" ;;
    Linux)  echo "  Linux build: dist/AllInOneSnapchatDownloader" ;;
    *)      echo "  Note: built for THIS platform only. To get the macOS .app or"
            echo "  Linux binary, build on that OS or use the GitHub Actions workflow." ;;
esac
