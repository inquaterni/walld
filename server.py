#!/usr/bin/env python
from argparse import ArgumentParser
from enum import Enum
from functools import wraps
# TODO: 1. Handle D-Bus connection failures
#       2. Provide status information via D-Bus properties
from mimetypes import guess_type
from pathlib import Path
from queue import Queue
from tomllib import TOMLDecodeError
from typing import Tuple, Any

from dasbus.server.interface import dbus_interface
from dasbus.typing import Str, Int, List, Bool
from gi import require_version
from watchdog.events import FileCreatedEvent, FileModifiedEvent
from watchdog.observers import Observer

require_version("Gio", "2.0")
from gi.repository.Gio import Subprocess, SubprocessFlags, io_error_quark, IOErrorEnum, Cancellable, Task
from gi.repository.GLib import Error, source_remove, SOURCE_CONTINUE, timeout_add_seconds, idle_add, SOURCE_REMOVE
from gi.repository.GObject import GObject
from dasbus.loop import EventLoop
from logging import Logger, getLogger, Formatter, DEBUG, StreamHandler, INFO, Handler
from logging.handlers import SysLogHandler, QueueListener, QueueHandler
from config import SERVICE
from os import urandom
from random import Random

from errors import (
    InvalidInterfaceNameError,
    NoFilesProvidedError,
    UnknownTimeUnitsError,
    VariableDoesNotExistError,
    VariableTypeError,
    VariableAttributeError,
    NoValidFilesProvidedError,
)
from toml_config import parse_config, Constant, Var, ConfigEventHandler, ConfigError, Config
from timer import Timer

class Mode(Enum):
    DEFAULT = 0
    DEBUG = 1


def queue_init(handler: Handler) -> tuple[QueueListener, QueueHandler]:
    queue = Queue()
    queue_listener = QueueListener(
        queue,
        handler
    )
    queue_listener.start()

    return queue_listener, QueueHandler(queue)


def logger_setup(logger: Logger, mode: Mode | None = None) -> QueueListener:
    logger.propagate = False
    logger.handlers.clear()

    match mode:
        case Mode.DEFAULT:
            handler = SysLogHandler("/dev/log", SysLogHandler.LOG_LOCAL1)
        case None:
            handler = SysLogHandler("/dev/log", SysLogHandler.LOG_LOCAL1)
        case _:
            handler = StreamHandler()

    formatter = Formatter("[%(levelname)s] %(threadName)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    queue_listener, queue_handler = queue_init(handler)
    logger.addHandler(queue_handler)

    match mode:
        case Mode.DEFAULT:
            logger.setLevel(INFO)
        case Mode.DEBUG:
            logger.setLevel(DEBUG)

    # User is responsible for stopping queue listener
    return queue_listener


# TODO: implement get functionality
@dbus_interface(SERVICE.interface_name)
class WallDaemon(GObject):
    def __init__(self, mode: Mode | None = None):
        super().__init__()
        self._current_index = 0
        self._current_wallpaper_file = None

        self._logger = getLogger(__class__.__name__)
        self.queue_listener = logger_setup(self._logger, mode)

        self.cancellable = Cancellable()
        self._timer: Timer | None = None

        # 4 bytes hardware based random, used once for seed
        self._seed = urandom(4)
        self._rng = Random(self._seed)
        self._shuffle_indexes = []

        self.config = None
        self.observer = None

    ############################
    ## DBUS INTERFACE METHODS ##
    ############################

    def SetSchedule(self, schedule: Int, units: Str) -> Str:
        return self._set_schedule(schedule, units)

    def SetFiles(self, files: List[Str]) -> Str:
        if len(files) == 0:
            raise NoFilesProvidedError()

        valid_files = self._validate_files(files)

        if not valid_files:
            raise NoValidFilesProvidedError()

        self.config.files = valid_files
        self._recalc_current_index(len(self.config.files))

        return "OK"

    def SetShuffle(self, shuffle: Bool) -> Str:
        self.config.shuffle = shuffle
        if self.config.shuffle:
            self._shuffle_indexes = self._generate_shuffle_indexes()

        return "OK"

    def GetInterfaces(self) -> List[Tuple[Str, List[Tuple[Str, Str]]]]:
        iface_indexes = list(index for index in range(len(self.config.ifaces)))
        return self._pack_interfaces(iface_indexes)

    def SetVariableValue(self, iface_name: Str, var_name: Str, value: Str) -> Str:
        iface = next((iface for iface in self.config.ifaces if iface.name == iface_name), None)
        if not iface:
            raise InvalidInterfaceNameError()

        var = iface.variables.get(var_name)

        if not var:
            raise VariableDoesNotExistError(var_name)

        val = self._deduce_var_type(var, value)

        try:
            var.set_value(val)
        except ValueError:
            raise VariableTypeError(type(var.value()), type(val))
        except AttributeError as e:
            raise VariableAttributeError(e)
        else:
            self._logger.info(f"Set variable `{var_name}` value `{value}`")
            return "OK"

    def GetActiveInterfaces(self) -> List[Tuple[Str, List[Tuple[Str, Str]]]]:
        iface_indexes = list(index for index in self.config.active_ifaces)
        return self._pack_interfaces(iface_indexes)

    def ActivateInterface(self, name: Str) -> Str:
        if name not in (item.name for item in self.config.ifaces):
            raise InvalidInterfaceNameError()
        for index, iface in enumerate(self.config.ifaces):
            if iface.name != name:
                continue
            if index in self.config.active_ifaces:
                return "Interface already active."
            self.config.active_ifaces.append(index)

        return "OK"

    def DeactivateInterface(self, name: Str) -> Str:
        if name not in (self.config.ifaces[index] for index in self.config.active_ifaces):
            return "Interface already inactive."
        for index, iface in enumerate(self.config.ifaces):
            if iface.name != name:
                continue
            if index in self.config.active_ifaces:
                self.config.active_ifaces.remove(index)
            else:
                raise InvalidInterfaceNameError()

        return "OK"

    def GetCurrentWallpaperFilename(self) -> Str:
        if self._current_wallpaper_file:
            return self._current_wallpaper_file
        else:
            return "Wallpaper was not set yet."

    def ForceWallpaperChange(self, no_reset: bool =  False) -> Str:
        self._logger.info(f"Force wallpaper change requested, no_reset={no_reset}.")
        self._set_next_wallpaper()
        if not no_reset:
            self._set_schedule(self.config.schedule, self.config.units.value)
        return "OK"

    def Pause(self, schedule: Int, units: Str) -> Str:
        self._logger.info("Pause requested.")

        if schedule:
            self._logger.info(f"\tPause interval: {schedule} {units}")
            self._timer.pause(self._str2units(schedule, units) * 1000)
        else:
            self._timer.pause()

        return "OK"

    def Resume(self):
        self._logger.info("Resuming timer...")
        self._timer.resume()

        return "OK"

    ###################
    ## CLASS METHODS ##
    ###################

    def run(self, config_path: str):
        self.config = parse_config(config_path)
        self._set_schedule(self.config.schedule, self.config.units.value)

        event_handler = ConfigEventHandler(self._on_config_created, self._on_config_modified)
        self.observer = Observer()
        self.observer.schedule(event_handler, config_path, event_filter=[FileCreatedEvent, FileModifiedEvent])
        self.observer.start()

    def _update_schedule(self, config: Config) -> bool:
        _ = self._set_schedule(config.schedule, config.units.value)
        return SOURCE_REMOVE

    # TODO: probably should save config path and check for it in order to prevent misfires/misconfigures
    def _on_config_created(self, event: FileCreatedEvent) -> None:
        self._logger.info("Config have been created, applying...")
        try:
            config = parse_config(event.src_path)

            if config.schedule != self.config.schedule or config.units != self.config.units:
                idle_add(self._update_schedule, config)

            self.config = config
        except (ConfigError, TOMLDecodeError) as e:
            self._logger.error("While parsing config exception was thrown.", exc_info=e)
        except Exception as e:
            self._logger.error("Unexpected error in config watcher", exc_info=e)
        else:
            self._logger.info("Config have been applied successfully.")

    def _on_config_modified(self, event: FileModifiedEvent) -> None:
        self._logger.info("Config have been modified, updating...")
        try:
            config = parse_config(event.src_path)

            if config.schedule != self.config.schedule or config.units != self.config.units:
                idle_add(self._update_schedule, config)

            self.config = config
        except (ConfigError, TOMLDecodeError) as e:
            self._logger.error("While parsing config exception was thrown.", exc_info=e)
        except Exception as e:
            self._logger.error("Unexpected error in config watcher", exc_info=e)
        else:
            self._logger.info("Config have been updated successfully.")

    def _set_schedule(self, schedule: Int, units: Str) -> Str:
        if self._timer:
            self._timer.stop()
            self._timer = None

        timeout = self._str2units(schedule, units)

        def timer_callback():
            self._logger.info("Timeout reached, setting next wallpaper")
            self._set_next_wallpaper()
            return SOURCE_CONTINUE

        if timeout > 0:
            self._timer = Timer.start_seconds(timeout, timer_callback)
            self._logger.info(f"Schedule set for {schedule} {units}.")
            return "OK"

        return "Given interval is zero."

    @staticmethod
    def _str2units(schedule: Int, units: Str):
        match units:
            case "s":
                timeout = schedule
            case "m":
                timeout = schedule * 60
            case "h":
                timeout = schedule * 3600
            case _:
                raise UnknownTimeUnitsError()
        return timeout

    def _recalc_current_index(self, files_length: int) -> None:
        self._current_index %= files_length

    def _generate_shuffle_indexes(self) -> List[Int]:
        if not self.config.files:
            return []

        indexes = list(range(len(self.config.files)))
        self._rng.shuffle(indexes)

        return indexes

    def _set_next_wallpaper(self) -> None:
        if not self.config.files:
            raise NoFilesProvidedError()
        else:
            if self.config.shuffle:
                if not self._shuffle_indexes or not self._current_index:
                    self._shuffle_indexes = self._generate_shuffle_indexes()

                index = self._shuffle_indexes[self._current_index]
            else:
                index = self._current_index

            task = Task.new(
                self,
                self.cancellable,
                self._set_wallpaper_finish,
                index,
            )

            # noinspection PyTypeChecker
            task.set_task_data(index)
            task.run_in_thread(self._set_wallpaper)

            self._current_index = (self._current_index + 1) % len(self.config.files)

    def _set_wallpaper(self, task, source_object, _, cancellable) -> None:
        index = task.get_task_data()
        if index >= len(self.config.files):
            task.return_error(Error(message=f"Index was out of bounds: {index}, size={len(self.config.files)}"))
            return
        for interface_index in self.config.active_ifaces:
            try:
                if cancellable.is_cancelled() and task.return_error_if_cancelled():
                    return

                proc = Subprocess.new(
                    self.config.ifaces[interface_index].formatted_args(
                        self.config.files[index]
                    ),
                    SubprocessFlags.STDERR_PIPE | SubprocessFlags.STDOUT_PIPE
                )

                proc.communicate_utf8_async(cancellable=cancellable, callback=self._communicate_finish)
            except Exception as e:
                self._logger.error(
                    f"Failed to set wallpaper for interface `{self.config.ifaces[interface_index].name}`.",
                    exc_info=e
                )
                task.return_error(Error(message=f"{type(e)}: {e}"))
            else:
                task.return_boolean(True)

    def _communicate_finish(self, proc: Subprocess, result) -> None:
        try:
            success, stdout, stderr = proc.communicate_utf8_finish(result)

            if success:
                self._logger.info("Communication finished successfully.")
            if stdout:
                self._logger.info(f"Subprocess output: '{stdout.strip()}'")
            if stderr:
                self._logger.error(f"Subprocess error output: '{stderr.strip()}'")
        except Error as e:
            if e.matches(io_error_quark(), IOErrorEnum.CANCELLED):
                self._logger.error("Communication canceled.")
            else:
                self._logger.error(f"Communication error: {e}")
        except Exception as e:
            self._logger.error(f"Communication error: {e}")

    def _set_wallpaper_finish(self, _, result, index: int) -> None:
        try:
            success = result.propagate_boolean()

            if success:
                self._logger.info("Wallpaper set successfully.")
                self._current_wallpaper_file = self.config.files[index]

        except Exception as e:
            self._logger.exception(f"Could not set wallpaper: {e}")

    @staticmethod
    def _deduce_var_type(var: Var, value: Str) -> Any:
        try:
            val = type(var.value())(value)
        except Exception:
            raise VariableTypeError(type(var.value()), type(value))
        else:
            return val

    # TODO: make normal packets for more flexible messaging
    def _pack_interfaces(self, iface_indexes: List[int]) -> List[Tuple[Str, List[Tuple[Str, Str]]]]:
        ifaces = []
        for iface_index in iface_indexes:
            iface = self.config.ifaces[iface_index]
            variables = []
            for name, var in iface.variables.items():
                if not isinstance(var, Constant):
                    variables.append((name, var.value().__str__()))
            ifaces.append((iface.name, variables))

        return ifaces

    def stop(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
        self.queue_listener.stop()

    def _validate_files(self, files: List[Str]) -> List[Str]:
        valid_files = []
        for file in files:
            filepath = Path(file).expanduser()

            if not filepath.exists():
                self._logger.warning(f"File does not exist: `{filepath}`, skipping ...")
                continue

            if filepath.is_dir():
                self._logger.warning(f"Given path is a directory, skipping ...")
                continue

            mime = guess_type(filepath)[0]
            if mime and mime.startswith("image/"):
                valid_files.append(str(filepath))
            elif mime:
                self._logger.warning(f"File `{filepath}` mime type is `{mime}` which is not viable, skipping ...")
            else:
                self._logger.warning(f"Cannot guess file `{filepath}` mime type, skipping ...")

        return valid_files


def main():
    config_path = Path("~/.config/walld/config.toml").expanduser()
    mode = Mode.DEFAULT

    parser = ArgumentParser(
        description="WallD D-Bus server.",
    )
    parser.add_argument(
        "-c",
        "--config_file",
        help="Path to TOML configuration file. Default is `~/.config/walld/config.toml`",
    )
    parser.add_argument(
        "-m",
        "--mode",
        help="Run mode of the server, either `default` or `debug`.",
        choices=["default", "debug"]
    )

    args = parser.parse_args()
    if args.config_file:
        config_path = Path(args.config_file).expanduser()
    if args.mode:
        mode = Mode.DEBUG if args.mode == "debug" else Mode.DEFAULT

    loop = EventLoop()

    logger = getLogger(__name__)
    logger_setup(logger, mode)

    logger.info(f"Starting WallD D-Bus service in {args.mode or 'default'} mode...")
    service = WallDaemon(mode)

    try:
        SERVICE.message_bus.publish_object(SERVICE.object_path, service)
        SERVICE.message_bus.register_service(SERVICE.service_name)

        logger.info(f"Service published at: {SERVICE.service_name}")
        logger.info("Service is running. Press Ctrl+C to stop.")

        service.run(str(config_path))
        loop.run()

    except KeyboardInterrupt:
        logger.info("Service stopped by user")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        logger.info("Closing session message bus.")
        loop.quit()
        service.stop()
        SERVICE.message_bus.disconnect()


if __name__ == "__main__":
    main()
