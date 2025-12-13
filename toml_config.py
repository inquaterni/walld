from abc import abstractmethod, ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Any
from tomllib import load
from pathlib import Path
from mimetypes import guess_type


class ConfigError(Exception):
    pass


class Units(Enum):
    SECONDS = "s"
    MINUTES = "m"
    HOURS = "h"

class Var(ABC):
    @abstractmethod
    def value(self):
        pass

class MutableVar(ABC):
    @abstractmethod
    def set_value(self, new):
        pass

@dataclass(frozen=True)
class Constant(Var):
    val: Any

    def value(self):
        return self.val

@dataclass
class Mutable(Var, MutableVar):
    val: Any

    def value(self):
        return self.val

    def set_value(self, new: Any):
        if not isinstance(new, type(self.val)):
            raise ValueError(f"Value of type `{type(self.val)}` cannot be assigned value of type `{type(new)}`.")
        self.val = new


@dataclass
class Enumeration(Var, MutableVar):
    current: Any
    options: List[Any] = field(default_factory=list)

    def value(self) -> Any:
        return self.current

    def set_value(self, new: Any):
        if not isinstance(new, type(self.current)):
            # TODO: add error message
            raise ValueError()
        if new not in self.options:
            raise ValueError(f"Enum `{self.__class__.__name__}` cannot be assigned value `{new}` - possible options: {self.options}")
        
        self.current = new



@dataclass
class Interface:
    name: str
    args: List[str]
    variables: dict[str, Var]

    def formatted_args(self, img_file: str) -> List[str]:
        result = []
        for arg in self.args:
            if "%" not in arg:
                result.append(arg)
            else:
                var_name = arg[1:]
                if var_name == "f":
                    result.append(img_file)
                elif var_name in self.variables:
                    result.append(self.variables[var_name].value())

        return result


@dataclass
class Config:
    schedule: int = 1
    units: Units = Units.HOURS
    shuffle: bool = True
    files: List[str] = field(default_factory=list)
    active_ifaces: List[int] = field(default_factory=list)
    ifaces: List[Interface] = field(default_factory=list)


@dataclass
class ConfigBuilder:
    config: Config = field(default_factory=Config)

    def apply_ifaces(self, ifaces_dict: dict[str, Any]) -> "ConfigBuilder":
        if not ifaces_dict:
            return self

        for name, args in ifaces_dict.items():
            if not isinstance(args, dict):
                iface = Interface(name, args, {})
                self.config.ifaces.append(iface)
            else:
                args = ifaces_dict[name].get("args")
                if not args:
                    raise ConfigError("Verbose interface declaration requires existence of field/variable `args`.")

                variables_dict = ifaces_dict[name].get("variables")
                variables = {}
                if variables_dict:
                    for k, v in variables_dict.items():
                        if not isinstance(args, dict):
                            # Enum variable declaration check
                            current = variables_dict[k].get("current")
                            if current is None:
                                value = variables_dict[k].get("value")
                                # Variable declaration check
                                if value is None:
                                    raise ConfigError("Verbose variable declaration lacks either `value` field or `current` field.")
                                const = variables_dict[k].get("const")
                                if const:
                                    variables[k] = Constant(value)
                                else:
                                    variables[k] = Mutable(value)
                                continue

                            # No `const` in enum constraint
                            if "const" in variables_dict[k].keys():
                                raise ConfigError("Enum type cannot be a constant variable")

                            options = variables_dict[k].get("options")
                            if options is None:
                                raise ConfigError("Enum type expected to have `options` field/variable.")

                            variables[k] = Enumeration(current, options)
                        else:
                            # Not verbose => constant
                            variables[k] = Constant(v)

                iface = Interface(name, args, variables)
                self.config.ifaces.append(iface)

        return self

    def apply_daemon_settings(self, daemon_dict: dict[str, Any]) -> "ConfigBuilder":
        if not daemon_dict:
            return self

        self.config.schedule = daemon_dict.get("schedule", self.config.schedule)
        try:
            self.config.units = Units(daemon_dict.get("units", self.config.units.value))
        except ValueError:
            raise ConfigError(f"Invalid units value {daemon_dict.get('units')}.")
        self.config.shuffle = daemon_dict.get("shuffle", self.config.shuffle)
        # Required parameter
        path_value = daemon_dict.get("path")
        if path_value:
            self.config.files = self._path_walk(path_value)

        self.config.active_ifaces = self._map_active_ifaces(
            daemon_dict.get("active_interfaces", self.config.active_ifaces), self.config.ifaces
        )

        return self

    @staticmethod
    def _map_active_ifaces(active_ifaces, ifaces) -> List[int]:
        if not active_ifaces:
            return []

        result = []
        for index, iface in enumerate(ifaces):
            if iface.name in active_ifaces:
                result.append(index)

        return result

    @staticmethod
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

    def build(self):
        return self.config


def parse_config(path: str) -> Config:
    p = Path(path).expanduser()
    if not p.exists():
        raise ConfigError(f"Given path {path} does not exist.")
    if not p.is_file():
        raise ConfigError(f"Given path is not a file: {path}")

    with open(p, "rb") as f:
        config_dict = load(f)

    config = ConfigBuilder() \
              .apply_ifaces(config_dict.get("Interfaces")) \
              .apply_daemon_settings(config_dict.get("Daemon")) \
              .build()

    return config

if __name__ == "__main__":
    config = parse_config("default.toml")
    print(config)
