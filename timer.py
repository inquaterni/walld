from typing import Any, Callable

from gi import require_version
require_version("Gio", "2.0")
from gi.repository.GLib import Error, source_remove, SOURCE_CONTINUE, timeout_add, get_monotonic_time, MainLoop, SOURCE_REMOVE


class ZeroNegativeIntervalError(ValueError):
    def __init__(self, *args):
        super().__init__("Interval is not within positive range.", *args)


class Timer:
    def __init__(self, interval: int, callback: Callable[..., Any], *c_args):
        """
        :param interval: Interval value in ms.
        :param callback: Callback on timer expiration.
        :param c_args: Callback arguments list.
        """
        self._interval = interval
        self._timer_tag = None
        self._start_point = 0
        self._callback = callback
        self._c_args = c_args
        self._remainder = None
        self._resume_tag = None

    @staticmethod
    def start(interval: int, callback: Callable[..., Any],  *args):
        if interval <= 0:
            raise ZeroNegativeIntervalError()

        timer = Timer(interval, callback, *args)
        timer._start_point = get_monotonic_time() // 1000
        timer._timer_tag = timeout_add(interval, timer._tick)

        return timer

    @staticmethod
    def start_seconds(interval: int, callback: Callable[..., Any], *args):
        if interval <= 0:
            raise ZeroNegativeIntervalError()

        timer = Timer(interval * 1000, callback, *args)
        timer._start_point = get_monotonic_time() // 1000
        timer._timer_tag = timeout_add(interval * 1000, timer._tick)

        return timer

    def pause(self, interval_ms: int = 0):
        if self._timer_tag is None: return

        source_remove(self._timer_tag)
        self._timer_tag = None

        now = get_monotonic_time() // 1000
        self._remainder = max(0, self._interval + self._start_point - now)

        if interval_ms:
            if self._resume_tag:
                source_remove(self._resume_tag)
                self._resume_tag = None

            self._resume_tag = timeout_add(interval_ms, self.resume)

    def resume(self):
        if self._timer_tag is not None or self._remainder is None: return
        if self._resume_tag:
            source_remove(self._resume_tag)
            self._resume_tag = None

        self._timer_tag = timeout_add(self._remainder, self._resume_callback)
        self._remainder = None

    def stop(self):
        if self._timer_tag is None: return
        source_remove(self._timer_tag)
        self._timer_tag = None
        if self._resume_tag is None: return
        source_remove(self._resume_tag)
        self._resume_tag = None

    def _tick(self) -> Any:
        self._start_point = get_monotonic_time() // 1000

        return self._callback(*self._c_args)

    def _resume_callback(self):
        result = self._callback(*self._c_args)
        if not result:
            self._timer_tag = None
        else:
            self._start_point = get_monotonic_time() // 1000
            self._timer_tag = timeout_add(self._interval, self._tick)

        return SOURCE_REMOVE


def __hello(msg: str):
    print(msg)
    return SOURCE_CONTINUE

if __name__ == "__main__":
    loop = MainLoop()

    timer_1 = Timer.start(10000, __hello, "Hello1")
    timer_2 = Timer.start(5000, __hello, "Hello2")
    timer_3 = Timer.start(9500, __hello, "Hello3")

    timeout_add(3500, lambda: timer_2.pause())
    timeout_add(7000, lambda: timer_2.resume())
    timeout_add(20000, loop.quit)

    loop.run()