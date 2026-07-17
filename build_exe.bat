@echo off
REM Single source of truth for the release build (issue #45). The .spec
REM uses collect_all('timezonefinder') to ensure the timezone polygons ship
REM with the EXE; any future tweaks belong in AllInOneSnapchatDownloader.spec,
REM not in flags here.
echo Building Snapchat Memories Downloader executable...
pyinstaller --clean AllInOneSnapchatDownloader.spec
echo.
echo Build complete! Check the dist folder for AllInOneSnapchatDownloader.exe
pause
