"""Network command catalog. Unsupported protocols fail before operation approval."""
from pathlib import Path
from agent.permissions.shell_syntax import command_after_prefixes, parse_shell

from integrations.git_push import push_command
from integrations.shell_http import curl_args

# Protocol-specific clients must freeze their payload before entering this allow set.
COMMANDS = {
    "NET-01/02": {"curl", "wget", "http", "https", "xh"},
    "NET-06": {"pip", "pip3", "uv", "poetry", "npm", "pnpm", "yarn", "bun", "cargo", "brew",
               "go", "docker", "podman", "apt", "apt-get", "dnf", "yum", "pacman"},
    "NET-07": {"twine", "gh", "glab", "vercel", "netlify", "fly", "wrangler"},
    "NET-08": {"ssh", "scp", "sftp", "rsync", "ftp", "lftp", "aws", "gcloud", "az", "kubectl", "helm"},
    "NET-13": {"dig", "nslookup", "host", "ping", "ping6", "traceroute", "nc", "ncat", "telnet", "openssl"},
    "NET-14": {"open", "xdg-open"},
    "NET-15": {"mail", "mailx", "sendmail", "swaks"},
    "NET-16": {"socat", "websocat", "grpcurl", "ngrok", "cloudflared"},
}
GIT_NETWORK = {"push", "fetch", "pull", "clone", "ls-remote", "submodule", "lfs"}
LOCAL_PACKAGE_VERBS = {
    "pip": {"list", "show", "check", "freeze"}, "pip3": {"list", "show", "check", "freeze"},
    "uv": {"run", "tree", "cache"}, "poetry": {"run", "show", "env"},
    "npm": {"run", "test", "list", "ls", "config"}, "pnpm": {"run", "test", "list", "exec"},
    "yarn": {"run", "test", "exec"}, "bun": {"run", "test"},
    "cargo": {"test", "check", "build", "fmt", "clippy", "metadata"},
    "brew": {"list", "info", "config"}, "go": {"test", "build", "fmt", "vet", "version"},
    "docker": {"ps", "images", "inspect", "version"}, "podman": {"ps", "images", "inspect", "version"},
}


def unsupported_network(command: str) -> str | None:
    segments, joins, issue = parse_shell(command)
    if (not joins and not issue and not any(token.operator or token.variable for token in segments[0])
            and (curl_args(command) is not None or push_command(command) is not None)):
        return None
    for segment in segments:
        words = [token.value for token in command_after_prefixes(segment) if not token.operator]
        if not words:
            continue
        name = Path(words[0]).name
        rest = words[1:]
        if name in COMMANDS["NET-06"] and ("--offline" in rest or "--no-index" in rest):
            continue
        if rest and rest[0] in LOCAL_PACKAGE_VERBS.get(name, set()):
            continue  # Local scripts/builds retain isolation; this does not grant direct networking.
        git_args = rest
        if name == "git":
            while git_args and git_args[0].startswith("-"):
                if git_args[0] in {"-C", "-c", "--git-dir", "--work-tree"}:
                    git_args = git_args[2:]
                elif git_args[0] in {"--no-pager", "--paginate"} or git_args[0].startswith(("--git-dir=", "--work-tree=")):
                    git_args = git_args[1:]
                else:
                    break
        if name == "git" and git_args[:2] in (["submodule", "status"], ["lfs", "ls-files"]):
            continue
        if name == "rsync" and not any(":" in value for value in rest):
            continue
        if name == "git" and (git_args[:1] and git_args[0] in GIT_NETWORK
                              or git_args[:2] == ["remote", "show"]):
            return "NET-03/04：此 Git 网络形态尚不能固定目的地与对象，拒绝执行；支持 HTTPS 分支推送"
        for rule, commands in COMMANDS.items():
            if name in commands:
                # Purely local invocations still run under the ordinary restricted Shell policy.
                if rest[:1] in (["--version"], ["-V"], ["--help"]):
                    continue
                return f"{rule}：此命令尚无逐请求检查能力，拒绝直接联网；HTTP 请使用受控 curl"
    return None
