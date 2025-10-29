#!/usr/bin/env python

# TODO: 1. Handle D-Bus connection failures
#       2. Provide status information via D-Bus properties
#       3. If possible fix `GLib-GIO-CRITICAL **: <time>: g_task_propagate_value: assertion 'task->result_destroy == value_free' failed`
from functools import wraps
from os.path import expanduser
from subprocess import run
from typing import Callable

from dasbus.server.interface import dbus_interface
from dasbus.typing import Str, Int, List, Bool
from gi import require_version

require_version("GLib", "2.0")
require_version("Gio", "2.0")
require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject
from dasbus.loop import EventLoop
from logging import Logger, getLogger, INFO, Formatter, DEBUG
from logging.handlers import SysLogHandler
from config import SERVICE
from os import urandom
from random import Random

from errors import (
    InvalidInterfaceNameError,
    NoFilesProvidedError,
    ServerNotRunningError,
    UnknownTimeUnitsError,
)
from toml_config import parse_config


def logger_setup(logger: Logger):
    logger.propagate = False
    logger.handlers.clear()
    handler = SysLogHandler("/dev/log", SysLogHandler.LOG_LOCAL1)
    formatter = Formatter("[%(levelname)s] %(threadName)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(DEBUG)


logger = getLogger(__name__)
logger_setup(logger)


def is_running(func: Callable):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not self._is_running:
            raise ServerNotRunningError()
        return func(self, *args, **kwargs)

    return wrapper


@dbus_interface(SERVICE.interface_name)
class WallDaemon(GObject.GObject):
    def __init__(self):
        super().__init__()
        self._is_running = False
        self._timer_id = None
        self._current_index = 0
        self._logger = getLogger(__class__.__name__)
        logger_setup(self._logger)

        # 4 bytes hardware based random, used once for seed
        self._seed = urandom(4)
        self._rng = Random(self._seed)
        self._shuffle_indexes = []

        self.config = None

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
    def GetInterfaces(self) -> List[Str]:
        return list(item.name for item in self.config.interfaces)

    @is_running
    def GetActiveInterfaces(self) -> List[Str]:
        return list(
            self.config.ifaces[index].name for index in self.config.active_ifaces
        )

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
        if name not in (item.name for item in self.config.ifaces):
            return "Interface is inactive."
        for index, iface in enumerate(self.config.ifaces):
            if iface.name != name:
                continue
            if index in self.config.active_ifaces:
                self.config.active_ifaces.remove(index)
            else:
                raise InvalidInterfaceNameError()

        return "OK"

    ###################
    ## CLASS METHODS ##
    ###################

    def run(self, config_path: str):
        self.config = parse_config(config_path)
        self._is_running = True
        self._set_schedule(self.config.schedule, self.config.units.value)

    def _set_schedule(self, schedule: Int, units: Str) -> Str:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
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
            return GLib.SOURCE_CONTINUE

        if timeout > 0:
            self._timer_id = GLib.timeout_add_seconds(timeout, timer_callback)
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

            task = Gio.Task.new(
                source_object=self,
                cancellable=None,  # TODO: add Cancellable object here
                callback=self._set_wallpaper_finish,
            )

            task.set_task_data(index)
            task.run_in_thread(self._set_wallpaper)

            self._current_index = (self._current_index + 1) % len(self.config.files)

    def _set_wallpaper(self, task, source_object, _, cancellable):
        index = task.get_task_data()

        try:
            for interface_index in self.config.active_ifaces:
                run(
                    self.config.ifaces[interface_index].formatted_args(
                        self.config.files[index]
                    ),
                    check=False,
                )

            task.return_boolean(True)
        except Exception as e:
            self._logger.error(
                f"Failed to set wallpaper for interface {interface_index}: {e}"
            )
            task.return_error(GLib.Error(message=f"{type(e).__name__}: {e}"))

    def _set_wallpaper_finish(self, _, result):
        try:
            success = result.propagate_value().get_boolean()

            if success:
                self._logger.info("Wallpaper set successfully.")

        except Exception as e:
            self._logger.exception(f"Could not set wallpaper: {e}")


def main():
    # TODO: add program argument for config
    config_path = expanduser("~/.config/walld/config.toml")

    loop = EventLoop()

    try:
        logger.info("Starting WallD DBus Service...")
        service = WallDaemon()

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
        logger.info("Closing Session Message Bus.")
        SERVICE.message_bus.disconnect()


if __name__ == "__main__":
    main()
