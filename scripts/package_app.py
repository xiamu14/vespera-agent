"""Build a self-contained local macOS app bundle."""
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP = DIST / "Vespera.app"
RESOURCES = APP / "Contents" / "Resources"


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def main():
    cargo = shutil.which("cargo")
    if cargo is None:
        raise SystemExit("Install Rust and make cargo available in PATH first.")
    run(cargo, "build", "--release", "--locked")
    executable = APP / "Contents" / "MacOS" / "vespera"
    executable.parent.mkdir(parents=True, exist_ok=True)
    RESOURCES.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "target" / "release" / "vespera", executable)
    iconset = DIST / "Vespera.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            output = iconset / f"icon_{points}x{points}{suffix}.png"
            run("sips", "-z", str(points * scale), str(points * scale),
                str(ROOT / "assets" / "app-icon.png"), "--out", str(output))
    run("iconutil", "-c", "icns", str(iconset), "-o", str(RESOURCES / "Vespera.icns"))
    info = {
        "CFBundleName": "Vespera",
        "CFBundleDisplayName": "Vespera",
        "CFBundleIdentifier": "dev.vespera.desktop",
        "CFBundleExecutable": "vespera",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "CFBundleIconFile": "Vespera.icns",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "14.0",
    }
    with (APP / "Contents" / "Info.plist").open("wb") as output:
        plistlib.dump(info, output)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", RESOURCES)
    run("plutil", "-lint", str(APP / "Contents" / "Info.plist"))
    run("codesign", "--force", "--sign", "-", str(APP))
    run("codesign", "--verify", "--deep", "--strict", str(APP))
    print(APP)


if __name__ == "__main__":
    main()
