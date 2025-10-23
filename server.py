#!/usr/bin/env python
from logging import Formatter, handlers

# TODO: 1. Implement async interface methods
#       2. Implement proper logging to journald
#       3. Handle D-Bus connection failures
#       4. Provide status information via D-Bus properties
from os.path import expanduser
from subprocess import run
from threading import Thread
from dasbus.server.interface import dbus_interface
from dasbus.typing import Str, Int, List, Bool
from dasbus.loop import EventLoop
from logging import Logger, getLogger, basicConfig, INFO
from logging.handlers import SysLogHandler
from config import SERVICE
from schedule import Job, every, run_pending, cancel_job
from time import sleep
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
    handler = SysLogHandler("/dev/log", SysLogHandler.LOG_LOCAL1)
    formatter = Formatter("[%(asctime)s %(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


basicConfig(level=INFO)
logger = getLogger(__name__)
logger_setup(logger)


@dbus_interface(SERVICE.interface_name)
class WallDaemon(object):
    def __init__(self):
        self._is_running = False
        self._scheduled_task: Job | None = None
        self._current_index = 0
        self._loop = EventLoop()
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

    def SetSchedule(self, schedule: Int, units: Str) -> Str:
        if self._is_running:
            if self._scheduled_task is not None:
                cancel_job(self._scheduled_task)
        else:
            raise ServerNotRunningError()

        match units:
            case "s":
                self._scheduled_task = every(schedule).seconds.do(
                    self._set_next_wallpaper
                )
            case "m":
                self._scheduled_task = every(schedule).minutes.do(
                    self._set_next_wallpaper
                )
            case "h":
                self._scheduled_task = every(schedule).hours.do(
                    self._set_next_wallpaper
                )
            case _:
                raise UnknownTimeUnitsError()

        return "OK"

    def SetFiles(self, files: List[Str]) -> Str:
        if not self._is_running:
            raise ServerNotRunningError()
        if len(files) == 0:
            raise NoFilesProvidedError()

        self.config.files = files
        self._recalc_current_index(len(self.config.files))

        return "OK"

    def SetShuffle(self, shuffle: Bool) -> Str:
        if not self._is_running:
            raise ServerNotRunningError()
        self.config.shuffle = shuffle
        if self.config.shuffle:
            self._shuffle_indexes = self._generate_shuffle_indexes()

        return "OK"

    def GetInterfaces(self) -> List[Str]:
        if not self._is_running:
            raise ServerNotRunningError()

        return list(map(lambda x: x.name, self.config.ifaces))

    def GetActiveInterfaces(self) -> List[Str]:
        if not self._is_running:
            raise ServerNotRunningError()
        return list(
            map(
                lambda x: x.name,
                (self.config.ifaces[index] for index in self.config.active_ifaces),
            )
        )

    def ActivateInterface(self, name: Str) -> Str:
        if not self._is_running:
            raise ServerNotRunningError()
        if name not in map(lambda x: x.name, self.config.ifaces):
            raise InvalidInterfaceNameError()
        for index, iface in enumerate(self.config.ifaces):
            if iface.name != name:
                continue
            if index in self.config.active_ifaces:
                return "Interface already active."
            self.config.active_ifaces.append(name)

        return "OK"

    def DeactivateInterface(self, name: Str) -> Str:
        if not self._is_running:
            raise ServerNotRunningError()
        if name not in map(lambda x: x.name, self.config.ifaces):
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
        self._is_running = True
        Thread(target=self._poll_sync, daemon=True).start()
        self.config = parse_config(config_path)
        # TODO: add backing private method
        self.SetSchedule(self.config.schedule, self.config.units.value)
        self._loop.run()

    def _recalc_current_index(self, files_length: int):
        self._current_index %= files_length

    def _generate_shuffle_indexes(self) -> List[Int]:
        if not self.config.files:
            return []

        indexes = list(range(len(self.config.files)))
        self._rng.shuffle(indexes)

        return indexes

    def _set_next_wallpaper(self):
        if not self._is_running:
            return
        else:
            if not self.config.files:
                return
            else:
                if self.config.shuffle:
                    if not self._shuffle_indexes or self._current_index == 0:
                        self._shuffle_indexes = self._generate_shuffle_indexes()

                    index = self._shuffle_indexes[self._current_index]
                    self._set_wallpaper(index)
                else:
                    self._set_wallpaper(self._current_index)

                self._current_index = (self._current_index + 1) % len(self.config.files)

    def _set_wallpaper(self, index: int):
        for interface_index in self.config.active_ifaces:
            run(
                self.config.ifaces[interface_index].formatted_args(
                    self.config.files[index]
                ),
                check=False,
            )

    def _poll_sync(self) -> None:
        while self._is_running:
            run_pending()
            sleep(1)


def main():
    # TODO: add program argument for config
    config_path = expanduser("~/.config/walld/config.toml")

    try:
        logger.info("Starting WallD DBus Service...")
        service = WallDaemon()

        SERVICE.message_bus.publish_object(SERVICE.object_path, service)
        SERVICE.message_bus.register_service(SERVICE.service_name)

        logger.info(f"Service published at: {SERVICE.service_name}")
        logger.info("Service is running. Press Ctrl+C to stop.")

        service.run(config_path)

    except KeyboardInterrupt:
        logger.info("Service stopped by user")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        logger.info("Closing Session Message Bus.")
        SERVICE.message_bus.disconnect()


if __name__ == "__main__":
    main()
