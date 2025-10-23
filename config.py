from dasbus.identifier import DBusServiceIdentifier, DBusObjectIdentifier
from dasbus.connection import SessionMessageBus


SERVICE = DBusServiceIdentifier(
    namespace=("com", "walld", "WallDaemon"), message_bus=SessionMessageBus()
)

ERROR = DBusObjectIdentifier(namespace=("com", "walld", "WallDaemon", "Error"))
