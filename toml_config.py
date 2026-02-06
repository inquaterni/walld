from abc import abstractmethod, ABC
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from mimetypes import guess_type
from pathlib import Path
from tomllib import load
from typing import List, Any, Callable

from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent


class ConfigError(Exception):
    pass


class ConditionalPredicateError(Exception):
    def __init__(self, pre: bool, *args: object):
        super().__init__(*args)
        self.pre_cond = pre

    def __str__(self):
        return "Precondition predicate failed: " if self.pre_cond else "Postcondition predicate failed: " + super().__str__()


class ContractError(Exception):
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
            raise ValueError(
                f"Value of type `{type(self.val)}` cannot be assigned value of type `{type(new)}`."
            )
        self.val = new


@dataclass
class Enumeration(Var, MutableVar):
    name: str
    current: Any
    options: List[Any] = field(default_factory=list)

    def value(self) -> Any:
        return self.current

    def set_value(self, new: Any):
        if not isinstance(new, type(self.current)):
            raise ValueError(
                f"Value of type `{type(self.current)}` cannot be assigned value of type `{type(new)}`."
            )
        if new not in self.options:
            raise AttributeError(
                f"Enum `{self.name}` cannot be assigned value `{new}` - possible options: {self.options}"
            )

        self.current = new


@dataclass
class Interface:
    name: str
    args: List[str]
    variables: dict[str, Var]
    pre_hook: list[list[str]] = field(default_factory=list)
    post_hook: list[list[str]] = field(default_factory=list)

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
                    result.append(str(self.variables[var_name].value()))

        return result

    def formatted_pre_hook(self, img_file: str) -> List[List[str]]:
        result = []
        for hook_command in self.pre_hook:
            for arg in hook_command:
                if "%" not in arg:
                    result.append(arg)
                else:
                    var_name = arg[1:]
                    if var_name == "f":
                        result.append(img_file)
                    elif var_name in self.variables:
                        result.append(str(self.variables[var_name].value()))

        return result

    def formatted_post_hook(self, img_file: str) -> List[List[str]]:
        result = []
        for hook_command in self.post_hook:
            for arg in hook_command:
                if "%" not in arg:
                    result.append(arg)
                else:
                    var_name = arg[1:]
                    if var_name == "f":
                        result.append(img_file)
                    elif var_name in self.variables:
                        result.append(str(self.variables[var_name].value()))

        return result


@dataclass
class Config:
    schedule: int = 1
    units: Units = Units.HOURS
    shuffle: bool = True
    files: List[str] = field(default_factory=list)
    active_ifaces: List[int] = field(default_factory=list)
    ifaces: List[Interface] = field(default_factory=list)


def contract(predicate: Callable[..., bool], *, pre: bool = True, post: bool = False, msg: str | None = None):
    if pre == post:
        raise ContractError("Contract cannot have pre- & post- effect")
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                if pre and not predicate(self, *args, **kwargs):
                    raise ConditionalPredicateError(True, msg)
            except Exception as e:
                if isinstance(e, ConditionalPredicateError): raise
                raise ContractError(f"Exception raised in pre-condition predicate: {e}")

            result = func(self, *args, **kwargs)

            try:
                if post and not predicate(self, result, *args, **kwargs):
                    raise ConditionalPredicateError(False, msg)
            except Exception as e:
                if isinstance(e, ConditionalPredicateError): raise
                raise ContractError(f"Exception raised in post-condition predicate: {e}")

            return result
        return wrapper
    return decorator


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
                    raise ConfigError(
                        "Verbose interface declaration requires existence of field/variable `args`."
                    )

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
                                    raise ConfigError(
                                        "Verbose variable declaration lacks either `value` field or `current` field."
                                    )
                                const = variables_dict[k].get("const")
                                if const:
                                    variables[k] = Constant(value)
                                else:
                                    variables[k] = Mutable(value)
                                continue

                            # No `const` in enum constraint
                            if "const" in variables_dict[k].keys():
                                raise ConfigError(
                                    "Enum type cannot be a constant variable"
                                )

                            options = variables_dict[k].get("options")
                            if options is None:
                                raise ConfigError(
                                    "Enum type expected to have `options` field/variable."
                                )

                            variables[k] = Enumeration(k, current, options)
                        else:
                            # Not verbose => constant
                            variables[k] = Constant(v)

                # Interface local hooks
                pre_hook = ifaces_dict[name].get("pre_hook")
                post_hook = ifaces_dict[name].get("post_hook")
                if pre_hook and isinstance(pre_hook[0], str):
                    pre_hook = [pre_hook]
                if post_hook and isinstance(post_hook[0], str):
                    post_hook = [post_hook]
                iface = Interface(name, args, variables, pre_hook or [], post_hook or [])
                self.config.ifaces.append(iface)

        return self

    @contract(lambda self, _: self.config.ifaces, msg="Interfaces are not parsed yet. This function must be ran after `apply_ifaces`")
    def apply_global_pre_hooks(self, hook_dict: dict[str, list[Any]] | None) -> "ConfigBuilder":
        if not hook_dict:
            return self
        for iface_name, commands in hook_dict.items():
            if iface_name == "*":
                for iface in self.config.ifaces:
                    if isinstance(commands[0], str):
                        commands = [commands]

                    iface.pre_hook.extend(commands)
            else:
                target_iface = next((iface for iface in self.config.ifaces if iface.name == iface_name), None)
                if not target_iface:
                    continue
                if isinstance(commands[0], str):
                    commands = [commands]

                target_iface.pre_hook.extend(commands)

        return self

    @contract(lambda self, _: self.config.ifaces, msg="Interfaces are not parsed yet. This function must be ran after `apply_ifaces`")
    def apply_global_post_hooks(self, hook_dict) -> "ConfigBuilder":
        if not hook_dict:
            return self
        for iface_name, commands in hook_dict.items():
            if iface_name == "*":
                for iface in self.config.ifaces:
                    if isinstance(commands[0], str):
                        commands = [commands]

                    iface.post_hook.extend(commands)
            else:
                target_iface = next((iface for iface in self.config.ifaces if iface.name == iface_name), None)
                if not target_iface:
                    continue
                if isinstance(commands[0], str):
                    commands = [commands]

                target_iface.post_hook.extend(commands)

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
        recursive = daemon_dict.get("recursive")
        if path_value:
            self.config.files = self._path_walk(path_value, recursive)

        self.config.active_ifaces = self._map_active_ifaces(
            daemon_dict.get("active_interfaces", self.config.active_ifaces),
            self.config.ifaces,
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

    def _path_walk(self, path: str, recursive: bool | None = None) -> List[str]:
        p = Path(path).expanduser()
        if not p.exists():
            raise ConfigError(f"Given path {path} does not exist.")
        if not p.is_dir():
            raise ConfigError(f"Given path is not a directory: {path}")
        else:
            files = list(self._iter_dir(p, recursive))
            return files

    def _iter_dir(self, p: Path, recursive: bool | None):
        for item in p.iterdir():
            if item.is_dir() and recursive:
                self._iter_dir(item, recursive)

            mime = guess_type(item)[0]
            if mime and mime.startswith("image/"):
                yield str(item)

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

    return (
        ConfigBuilder()
        .apply_ifaces(config_dict.get("Interfaces"))
        .apply_global_pre_hooks(config_dict.get("PreHooks"))
        .apply_global_post_hooks(config_dict.get("PostHooks"))
        .apply_daemon_settings(config_dict.get("Daemon"))
        .build()
    )


class ConfigEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        on_created_cb: callable(FileCreatedEvent),
        on_modified_cb: callable(FileModifiedEvent),
    ):
        super().__init__()
        self.on_created_cb = on_created_cb
        self.on_modified_cb = on_modified_cb

    def on_created(self, event: FileCreatedEvent) -> None:
        super().on_created(event)
        try:
            self.on_created_cb(event)
        except Exception:
            return

    # TODO: Maybe add warning about deletion of dir/config file
    # def on_deleted(self, event: DirDeletedEvent | FileDeletedEvent) -> None:
    #     super().on_deleted(event)
    #
    #     what = "directory" if event.is_directory else "file"
    #     self.logger.info("Deleted %s: %s", what, event.src_path)

    def on_modified(self, event: FileModifiedEvent) -> None:
        super().on_modified(event)
        try:
            self.on_modified_cb(event)
        except Exception:
            return


if __name__ == "__main__":
    config = parse_config("default.toml")
    print(config)
