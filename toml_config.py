from dataclasses import dataclass, field
from enum import Enum
from typing import List
from tomllib import load
from pathlib import Path
from mimetypes import guess_type


class ConfigError(Exception):
    pass


class Units(Enum):
    SECONDS = "s"
    MINUTES = "m"
    HOURS = "h"


@dataclass
class Interface:
    name: str
    args: List[str]

    # **kwargs is for future "dynamic" variables
    def formatted_args(self, img_file: str, **kwargs) -> List[str]:
        result = []
        for arg in self.args:
            if "%" not in arg:
                result.append(arg)
            else:
                # RN no variables support :(
                match arg[1:]:
                    case "f":
                        result.append(img_file)

        return result


@dataclass
class Config:
    schedule: int = 1
    units: Units = Units.HOURS
    shuffle: bool = True
    files: List[str] = field(default_factory=list)
    active_ifaces: List[int] = field(default_factory=list)
    ifaces: List[Interface] = field(default_factory=list)


def parse_config(path: str) -> Config:
    p = Path(path).expanduser()
    if not p.exists():
        raise ConfigError(f"Given path {path} does not exist.")
    if not p.is_file():
        raise ConfigError(f"Given path is not a file: {path}")

    with open(p, "rb") as f:
        config_dict = load(f)

    config = Config()
    config = _apply_ifaces(config_dict.get("Interfaces"), config)
    config = _apply_daemon_settings(config_dict.get("Daemon"), config)

    return config


def _apply_daemon_settings(daemon_dict, config: Config) -> Config:
    if not daemon_dict:
        return config

    config.schedule = daemon_dict.get("schedule", config.schedule)
    try:
        config.units = Units(daemon_dict.get("units", config.units.value))
    except ValueError:
        raise ConfigError(f"Invalid units value {daemon_dict.get('units')}.")
    config.shuffle = daemon_dict.get("shuffle", config.shuffle)
    # Required parameter
    path_value = daemon_dict.get("path")
    if path_value:
        config.files = _path_walk(path_value)

    config.active_ifaces = _map_active_ifaces(
        daemon_dict.get("active_interfaces", config.active_ifaces), config.ifaces
    )

    return config


def _map_active_ifaces(active_ifaces, ifaces) -> List[int]:
    if not active_ifaces:
        return []

    result = []
    for index, iface in enumerate(ifaces):
        if iface.name in active_ifaces:
            result.append(index)

    return result


def _path_walk(path: str) -> List[str]:
    p = Path(path).expanduser()
    if not p.exists():
        raise ConfigError(f"Given path {path} does not exist.")
    if not p.is_dir():
        raise ConfigError(f"Given path is not a directory: {path}")
    else:
        files = []
        for item in p.iterdir():
            if not item.is_file():
                continue
            mime = guess_type(item)[0]
            if mime and mime.startswith("image/"):
                files.append(str(item))

        return files


def _apply_ifaces(ifaces_dict, config: Config) -> Config:
    if not ifaces_dict:
        return config

    for name, args in ifaces_dict.items():
        iface = Interface(name, args)
        config.ifaces.append(iface)

    return config


if __name__ == "__main__":
    config = parse_config("default.toml")
    print(config)
