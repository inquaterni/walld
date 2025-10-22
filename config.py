from dasbus.identifier import DBusServiceIdentifier
from dasbus.connection import SessionMessageBus


SERVICE = DBusServiceIdentifier(
    namespace=("com", "walld", "WallDaemon"), message_bus=SessionMessageBus()
)

