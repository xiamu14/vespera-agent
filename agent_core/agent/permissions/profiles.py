from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionProfile:
    read_only: bool
    sandboxed_shell: bool


PROFILES = {
    "coding": PermissionProfile(read_only=False, sandboxed_shell=True),
    "review": PermissionProfile(read_only=True, sandboxed_shell=False),
}
