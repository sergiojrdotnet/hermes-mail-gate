import os
from pathlib import Path


def hermes_root() -> Path:
    return Path(os.environ.get("MAIL_GATE_HERMES_ROOT") or Path.home() / ".hermes")


def data_dir() -> Path:
    path = hermes_root() / "mail-gate"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def profile_dir(bot: str) -> Path:
    return hermes_root() / "profiles" / bot


def read_env_file(path: Path) -> dict[str, str]:
    values = {}
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def profile_env(bot: str) -> dict[str, str]:
    return read_env_file(profile_dir(bot) / ".env")


def default_env() -> dict[str, str]:
    return read_env_file(hermes_root() / ".env")
