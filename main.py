#!/usr/bin/env python3
import argparse
from dasbus.error import DBusError

from config import SERVICE


# class CustomHelpFormatter(argparse.RawDescriptionHelpFormatter):
#     def format_help(self):
#         help_text = super().format_help()
# try:
#     proxy = SERVICE.message_bus.get_proxy(SERVICE.service_name, SERVICE.object_path)
# interfaces = proxy.GetInterfaces()

#     help_text += "\n+------------------------------------------+\n"
#     help_text += "| " + "INTERFACES".center(40) + " |"
#     help_text += "\n+------------------------------------------+\n"
#     if interfaces and "ERROR:" not in interfaces[0]:
#         for iface_name in interfaces:
#             help_text += f"    - {iface_name}\n"
#     elif interfaces:
#         help_text += f"    (Server running but reported: {interfaces[0]})\n"
#     else:
#         help_text += "    (No interfaces reported by server)\n"
# except DBusError as e:
#     help_text += "\n+------------------------------------------+\n"
#     help_text += "| " + "INTERFACES".center(40) + " |"
#     help_text += "\n+------------------------------------------+\n"
#     help_text += f"    (Could not connect to D-Bus service: {e.name})\n"
# except Exception:
#     help_text += "\n+------------------------------------------+\n"
#     help_text += "| " + "INTERFACES".center(40) + " |"
#     help_text += "\n+------------------------------------------+\n"
#     help_text += "    (Could not connect to D-Bus service to list interfaces)\n"
# return help_text


def main():
    parser = argparse.ArgumentParser(
        description="Command-line client for the WallD D-Bus service.",
    )

    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Action to perform"
    )

    schedule_parser = subparsers.add_parser(
        "schedule", help="Set the wallpaper rotation schedule."
    )
    schedule_parser.add_argument("value", type=int, help="Number of units")
    schedule_parser.add_argument(
        "units",
        choices=["s", "m", "h"],
        help="Time units (s=seconds, m=minutes, h=hours)",
    )

    files_parser = subparsers.add_parser(
        "files", help="Set the list of wallpaper files."
    )
    files_parser.add_argument(
        "files", nargs="+", help="One or more paths to wallpaper files"
    )

    shuffle_parser = subparsers.add_parser(
        "shuffle", help="Enable or disable shuffle mode."
    )
    shuffle_parser.add_argument(
        "state", choices=["on", "off"], help="Turn shuffle 'on' or 'off'"
    )

    subparsers.add_parser(
        "get-interfaces", help="List all interfaces defined in the server config."
    )

    subparsers.add_parser("get-active-interfaces", help="List all active interfaces.")

    activate_parser = subparsers.add_parser(
        "activate-interface", help="Activate a defined interface."
    )
    activate_parser.add_argument("name", help="Name of the interface to activate")

    deactivate_parser = subparsers.add_parser(
        "deactivate-interface", help="Deactivate a defined interface."
    )
    deactivate_parser.add_argument("name", help="Name of the interface to deactivate")

    args = parser.parse_args()

    try:
        proxy = SERVICE.message_bus.get_proxy(SERVICE.service_name, SERVICE.object_path)
    except DBusError as e:
        print(f"Error: Could not connect to D-Bus service '{SERVICE.service_name}'.")
        print(f"Details: {e.name}")
        print("Is the walld server running?")
        exit(1)

    result = ""
    try:
        if args.command == "schedule":
            result = proxy.SetSchedule(args.value, args.units)

        elif args.command == "files":
            result = proxy.SetFiles(args.files)

        elif args.command == "shuffle":
            result = proxy.SetShuffle(args.state == "on")

        elif args.command == "get-interfaces":
            interfaces = proxy.GetInterfaces()
            if interfaces and "ERROR:" not in interfaces[0]:
                print("Available interfaces:")
                for iface in interfaces:
                    print(f"- {iface}")
            elif interfaces:
                result = interfaces[0]
            else:
                print("No interfaces found.")

        elif args.command == "get-active-interfaces":
            interfaces = proxy.GetActiveInterfaces()
            if interfaces and "ERROR:" not in interfaces[0]:
                print("Active interfaces:")
                for iface in interfaces:
                    print(f"- {iface}")
            elif interfaces:
                result = interfaces[0]
            else:
                print("No active interfaces.")

        elif args.command == "activate-interface":
            result = proxy.ActivateInterface(args.name)

        elif args.command == "deactivate-interface":
            result = proxy.DeactivateInterface(args.name)

        if result:
            print(result)

    except DBusError as e:
        exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        exit(1)


if __name__ == "__main__":
    main()

