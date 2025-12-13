from dasbus.error import ErrorMapper, DBusError, get_error_decorator
from config import ERROR

error_mapper = ErrorMapper()
dbus_error = get_error_decorator(error_mapper)


@dbus_error(ERROR.interface_name + "ServerNotRunningError")
class ServerNotRunningError(DBusError):
    def __init__(self, *args) -> None:
        super().__init__("Server is not running.", *args)


@dbus_error(ERROR.interface_name + "UnknownTimeUnitsError")
class UnknownTimeUnitsError(DBusError):
    def __init__(self, *args: object) -> None:
        super().__init__("Unknown time units were provided.", *args)


@dbus_error(ERROR.interface_name + "NoFilesProvidedError")
class NoFilesProvidedError(DBusError):
    def __init__(self, *args: object) -> None:
        super().__init__("No files were provided.", *args)


@dbus_error(ERROR.interface_name + "InvalidInterfaceNameError")
class InvalidInterfaceNameError(DBusError):
    def __init__(self, *args: object) -> None:
        super().__init__("Invalid interface name.", *args)

@dbus_error(ERROR.interface_name + "VariableDoesNotExistError")
class VariableDoesNotExistError(DBusError):
    def __init__(self, var_name: str, *args: object):
        super().__init__(f"Variable `{var_name}` does not exist", *args)

@dbus_error(ERROR.interface_name + "VariableTypeError")
class VariableTypeError(DBusError):
    def __init__(self, var_type, arg_type, *args: object):
        super().__init__(f"Variable of type {var_type} cannot be assigned with value of type {arg_type}.", *args)
