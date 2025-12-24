#!/usr/bin/env python

# TODO: 1. Handle D-Bus connection failures
#       2. Provide status information via D-Bus properties
from functools import wraps
from os.path import expanduser
from queue import Queue
from subprocess import run
from sys import argv, exc_info
from tomllib import TOMLDecodeError
from typing import Callable, Tuple, Any

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
from logging import Logger, getLogger, Formatter, DEBUG, StreamHandler
from logging.handlers import SysLogHandler, QueueListener, QueueHandler
from config import SERVICE
from os import urandom
from random import Random

from errors import (
    InvalidInterfaceNameError,
    NoFilesProvidedError,
    ServerNotRunningError,
    UnknownTimeUnitsError, VariableDoesNotExistError, VariableTypeError, VariableAttributeError,
)
from toml_config import parse_config, Constant, Var, ConfigEventHandler, ConfigError, Config


def logger_setup(logger: Logger) -> QueueListener:
    logger.propagate = False
    logger.handlers.clear()
    handler = SysLogHandler("/dev/log", SysLogHandler.LOG_LOCAL1)
    # handler = StreamHandler()
    formatter = Formatter("[%(laevelname)s] %(threadName)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    queue = Queue()
    queue_listener = QueueListener(
        queue,
        handler
    )
    queue_listener.start()
    logger.addHandler(QueueHandler(queue))
    logger.setLevel(DEBUG)

    # User is responsible for stopping queue listener
    return queue_listener


logger = getLogger(__name__)
logger_setup(logger)


def is_running(func: Callable):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self._is_running:
            raise ServerNotRunningError()
        return func(self, *args, **kwargs)

    return wrapper


# TODO: add ability to change enum variables through DBus interface
# TODO: implement get functionality
@dbus_interface(SERVICE.interface_name)
class WallDaemon(GObject):
    def __init__(self):
        super().__init__()
        self._is_running = False
        self._timer_id = None
        self._current_index = 0
        self._logger = getLogger(__class__.__name__)
        self.queue_listener = logger_setup(self._logger)

        self.cancellable = Cancellable()
        # 4 bytes hardware based random, used once for seed
        self._seed = urandom(4)
        self._rng = Random(self._seed)
        self._shuffle_indexes = []

        self.config = None
        self.observer = None

    ############################
    ## DBUS INTERFACE METHODS ##
    ############################

    @is_running
    def SetSchedule(self, schedule: Int, units: Str) -> Str:
        return self._set_schedule(schedule, units)

    @is_running
    def SetFiles(self, files: List[Str]) -> Str:
        if len(files) == 0:
            raise NoFilesProvidedError()

        self.config.files = files
        self._recalc_current_index(len(self.config.files))

        return "OK"

    @is_running
    def SetShuffle(self, shuffle: Bool) -> Str:
        self.config.shuffle = shuffle
        if self.config.shuffle:
            self._shuffle_indexes = self._generate_shuffle_indexes()

        return "OK"

    @is_running
    def GetInterfaces(self) -> List[Tuple[Str, List[Tuple[Str, Str]]]]:
        iface_indexes = list(index for index in range(len(self.config.ifaces)))
        return self._pack_interfaces(iface_indexes)

    @is_running
    def SetVariableValue(self, iface_name: Str, var_name: Str, value: Str) -> Str:
        iface_indexes = tuple(index for index in range(len(self.config.ifaces)) if iface_name == self.config.ifaces[index].name)
        if len(iface_indexes) < 1:
            raise InvalidInterfaceNameError()

        index = iface_indexes[0]
        var = self.config.ifaces[index].variables.get(var_name)
        val = self._deduce_var_type(var, value)
        if not var:
            raise VariableDoesNotExistError(var_name)

        try:
            var.set_value(val)
        except ValueError:
            raise VariableTypeError(type(var.value()), type(val))
        except AttributeError as e:
            raise VariableAttributeError(e)
        else:
            self._logger.info(f"Set variable `{var_name}` value `{value}`")
            return "OK"

    @is_running
    def GetActiveInterfaces(self) -> List[Tuple[Str, List[Tuple[Str, Str]]]]:
        iface_indexes = list(index for index in self.config.active_ifaces)
        return self._pack_interfaces(iface_indexes)

    @is_running
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

    @is_running
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

    @is_running
    def GetCurrentWallpaperFilename(self) -> Str:
        if self.config.shuffle:
            if self._shuffle_indexes:
                index = self._shuffle_indexes[self._current_index - 1]
            else:
                return "Wallpaper was not set yet."
        else:
            index = self._current_index

        return self.config.files[index]

    @is_running
    def ForceWallpaperChange(self) -> Str:
        self._logger.info("Force wallpaper change.")
        self._set_next_wallpaper()
        self._set_schedule(self.config.schedule, self.config.units.value)
        return "OK"

    ###################
    ## CLASS METHODS ##
    ###################

    def run(self, config_path: str):
        self.config = parse_config(config_path)
        self._is_running = True
        self._set_schedule(self.config.schedule, self.config.units.value)

        event_handler = ConfigEventHandler(self._on_config_created, self._on_config_modified)
        self.observer = Observer()
        self.observer.schedule(event_handler, config_path, event_filter=[FileCreatedEvent, FileModifiedEvent])
        self.observer.start()

    def _update_schedule(self, config: Config):
        _ = self._set_schedule(config.schedule, config.units.value)
        return SOURCE_REMOVE

    # TODO: probably should save config path and check for it in order to prevent misfires/misconfigures
    def _on_config_created(self, event: FileCreatedEvent):
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

    def _on_config_modified(self, event: FileModifiedEvent):
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
        if self._timer_id:
            source_remove(self._timer_id)
            self._timer_id = None

        match units:
            case "s":
                timeout = schedule
            case "m":
                timeout = schedule * 60
            case "h":
                timeout = schedule * 3600
            case _:
                raise UnknownTimeUnitsError()

        def timer_callback():
            self._logger.info("Timeout reached, setting next wallpaper")
            self._set_next_wallpaper()
            return SOURCE_CONTINUE

        if timeout > 0:
            self._timer_id = timeout_add_seconds(timeout, timer_callback)
            self._logger.info(f"Schedule set for {schedule} {units}.")
            return "OK"

        return "Given interval is zero."

    def _recalc_current_index(self, files_length: int):
        self._current_index %= files_length

    def _generate_shuffle_indexes(self) -> List[Int]:
        if not self.config.files:
            return []

        indexes = list(range(len(self.config.files)))
        self._rng.shuffle(indexes)

        return indexes

    @is_running
    def _set_next_wallpaper(self):
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
                source_object=self,
                cancellable=self.cancellable,
                callback=self._set_wallpaper_finish,
            )

            task.set_task_data(index)
            task.run_in_thread(self._set_wallpaper)

            self._current_index = (self._current_index + 1) % len(self.config.files)

    def _set_wallpaper(self, task, source_object, _, cancellable):
        index = task.get_task_data()
        if index >= len(self.config.files):
            task.return_error(Error(message=f"Index was out of bounds: {index}, size={len(self.config.files)}"))
            return
        try:
            for interface_index in self.config.active_ifaces:
                if cancellable.is_cancelled() and task.return_error_if_cancelled():
                    return

                proc = Subprocess.new(
                    self.config.ifaces[interface_index].formatted_args(
                        self.config.files[index]
                    ),
                    SubprocessFlags.STDERR_PIPE | SubprocessFlags.STDOUT_PIPE
                )

                proc.communicate_utf8_async(cancellable=cancellable, callback=self._communicate_finish)

            task.return_boolean(True)
        except Exception as e:
            self._logger.error(
                f"Failed to set wallpaper for interface {self.config.ifaces[interface_index].name}.",
                exc_info=e
            )
            task.return_error(Error(message=f"{type(e)}: {e}"))

    def _communicate_finish(self, proc: Subprocess, result):
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

    def _set_wallpaper_finish(self, _, result):
        try:
            success = result.propagate_boolean()

            if success:
                self._logger.info("Wallpaper set successfully.")

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
        self.observer.stop()
        self.observer.join()
        self.queue_listener.stop()


def main(argv):
    if len(argv) == 1:
        config_path = expanduser("~/.config/walld/config.toml")
    elif len(argv) == 2:
        config_path = expanduser(argv[1])
    else:
        print("Wrong argument count.")
        exit(1)

    loop = EventLoop()

    logger.info("Starting WallD DBus Service...")
    service = WallDaemon()

    try:
        SERVICE.message_bus.publish_object(SERVICE.object_path, service)
        SERVICE.message_bus.register_service(SERVICE.service_name)

        logger.info(f"Service published at: {SERVICE.service_name}")
        logger.info("Service is running. Press Ctrl+C to stop.")

        service.run(config_path)
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
    main(argv)
