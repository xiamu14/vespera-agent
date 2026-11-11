"""Build a self-contained local macOS app bundle."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
from tempfile import TemporaryDirectory

from package_agent import package_agent, sign

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP = DIST / "Vespera.app"


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def main():
    cargo = shutil.which("cargo")
    if cargo is None:
        raise SystemExit("Install Rust and make cargo available in PATH first.")
    run(cargo, "build", "--release", "--locked")
    DIST.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="vespera-build-", dir=DIST) as temp:
        staged = Path(temp) / APP.name
        build_app(staged)
        if APP.exists():
            shutil.rmtree(APP)
        staged.rename(APP)
    print(APP)


def build_app(app):
    contents = app / "Contents"
    resources = contents / "Resources"
    signing_identity = os.environ.get("VESPERA_SIGNING_IDENTITY", "-")
    executable = contents / "MacOS" / "vespera"
    executable.parent.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "target" / "release" / "vespera", executable)
    package_agent(contents, signing_identity=signing_identity)
    iconset = DIST / "Vespera.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            output = iconset / f"icon_{points}x{points}{suffix}.png"
            run("sips", "-z", str(points * scale), str(points * scale),
                str(ROOT / "assets" / "app-icon.png"), "--out", str(output))
    run("iconutil", "-c", "icns", str(iconset), "-o", str(resources / "Vespera.icns"))
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
    with (contents / "Info.plist").open("wb") as output:
        plistlib.dump(info, output)
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", resources)
    run("plutil", "-lint", str(contents / "Info.plist"))
    sign(app, signing_identity)
    run("codesign", "--verify", "--deep", "--strict", str(app))


if __name__ == "__main__":
    main()
