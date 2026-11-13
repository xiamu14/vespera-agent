"""Stage an in-process CPython runtime and Agent Core for a macOS bundle."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parent.parent
CODER = ROOT.parent / "coder-cli"
MACHO = {
    bytes.fromhex(magic) for magic in ("feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "cafebabf")
}


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def python_layout(python):
    script = """
import json, sys, sysconfig
assert sys.version_info[:2] == (3, 13)
print(json.dumps([sysconfig.get_config_var(name) for name in ('LIBDIR', 'LDLIBRARY', 'INCLUDEPY')]))
"""
    lib, name, include = json.loads(subprocess.check_output([python, "-c", script]))
    return Path(lib) / name, Path(include)


def stage_agent(contents, python):
    resources = contents / "Resources"
    frameworks = contents / "Frameworks"
    frameworks.mkdir(parents=True, exist_ok=True)
    library, headers = python_layout(python)
    home = resources / "python"
    stdlib = home / "lib" / "python3.13"
    shutil.copytree(library.parent / "python3.13", stdlib, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("site-packages", "__pycache__"))
    shutil.copytree(ROOT / "agent_core" / "vespera_agent", resources / "agent_core" / "vespera_agent",
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("agent", "context", "integrations", "storage", "tools", "prompts", "skills"):
        shutil.copytree(CODER / name, resources / "coder-cli" / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(CODER / "provider.jsonc", resources / "coder-cli")
    embedded = frameworks / library.name
    shutil.copy2(library, embedded)
    os.chmod(embedded, 0o755)
    run("install_name_tool", "-id", f"@loader_path/{library.name}", embedded)
    bridge = frameworks / "libvespera_agent.dylib"
    run("xcrun", "clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-dynamiclib",
        "-mmacosx-version-min=14.0", "-I", headers, ROOT / "agent_core" / "embed.c",
        embedded, "-o", bridge)
    return stdlib / "site-packages"


def sign(path, identity):
    options = [] if identity == "-" else ["--options", "runtime", "--timestamp"]
    run("codesign", "--force", "--sign", identity, *options, path)


def package_agent(contents, *, signing_identity="-"):
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("Install uv to package Ani Bot's Python runtime.")
    with TemporaryDirectory(prefix="vespera-package-") as temp:
        staging = Path(temp)
        python = subprocess.check_output(
            [uv, "python", "find", "--managed-python", "3.13"], text=True,
        ).strip()
        target = stage_agent(contents, python)
        requirements = staging / "requirements.txt"
        run(uv, "export", "--project", CODER, "--locked", "--no-dev", "--no-emit-workspace",
            "--output-file", requirements, "--quiet")
        run(uv, "pip", "sync", "--python", python, "--target", target, "--link-mode", "copy", requirements)

    run(str(target / "bin" / "rg"), "--version")

    for directory in ("Frameworks", "Resources/python"):
        for path in (contents / directory).rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                magic = stream.read(4)
            if magic in MACHO and path.suffix != ".class":
                sign(path, signing_identity)
