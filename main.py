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
        description="Command-line client for the WallD D-Bus service.\nManage wallpapers, schedules, and backend interfaces.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Action to perform"
    )

    schedule_parser = subparsers.add_parser(
        "schedule",
        help="Configure the automatic wallpaper rotation interval."
    )
    schedule_parser.add_argument(
        "value", type=int, help="Time value (e.g., 30)"
    )
    schedule_parser.add_argument(
        "units",
        choices=["s", "m", "h"],
        help="Time units (s=seconds, m=minutes, h=hours)",
    )

    files_parser = subparsers.add_parser(
        "files",
        help="Update the list of wallpapers (replaces current list)."
    )
    files_parser.add_argument(
        "files", nargs="+", help="Paths to image files to use as wallpapers"
    )

    shuffle_parser = subparsers.add_parser(
        "shuffle",
        help="Toggle random wallpaper playback order."
    )
    shuffle_parser.add_argument(
        "state", choices=["on", "off"], help="Enable ('on') or disable ('off') shuffle"
    )

    subparsers.add_parser(
        "current-wallpaper",
        help="Print the filename of the currently displayed wallpaper."
    )

    subparsers.add_parser(
        "list",
        help="Show all available backend interfaces and their settings."
    )

    subparsers.add_parser(
        "force-change",
        help="Force wallpaper change."
    )

    set_help_text = (
        "Modify interface variables or change interface state.\n\n"
        "Supported formats:\n"
        "  1. Space-separated:  walld set <interface> <var> <value>\n"
        "  2. Dot-separated:    walld set <interface>.<var> <value>\n"
        "  3. State change:     walld set <interface> <enabled|disabled>"
    )
    set_parser = subparsers.add_parser(
        "set",
        help="Set configuration variables or enable/disable interfaces.",
        description=set_help_text,
        formatter_class=argparse.RawTextHelpFormatter
    )
    set_parser.add_argument(
        "args",
        nargs="+",
        metavar="ARGS",
        help="Arguments defining what to set (see examples above)"
    )
    subparsers.add_parser(
        "list-active",
        help="List only the interfaces that are currently running."
    )

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

        elif args.command == "current-wallpaper":
            result = proxy.GetCurrentWallpaperFilename()

        elif args.command == "list":
            interfaces = proxy.GetInterfaces()
            if interfaces and "ERROR:" not in interfaces[0]:
                print("Available interfaces:")
                for iface, variables in interfaces:
                    print(f"- {iface}")
                    if variables:
                        print("\tVariables:")
                        for var, current_value in variables:
                            print(f"\t- {var} = '{current_value}'")
            elif interfaces:
                result = interfaces[0]
            else:
                print("No interfaces found.")
        elif args.command == "force-change":
            result = proxy.ForceWallpaperChange()
        elif args.command == "set":
            inputs = args.args

            if len(inputs) == 3:
                interface_name = inputs[0]
                var_name = inputs[1]
                value = inputs[2]
                result = proxy.SetVariableValue(interface_name, var_name, value)
            elif len(inputs) == 2:
                if "." in inputs[0]:
                    parts = inputs[0].split(".", 1)
                    interface_name = parts[0]
                    var_name = parts[1]
                    value = inputs[1]
                    result = proxy.SetVariableValue(interface_name, var_name, value)
                else:
                    if inputs[1].lower() == "enabled":
                        result = proxy.ActivateInterface(inputs[0])
                    elif inputs[1].lower() == "disabled":
                        result = proxy.DeactivateInterface(inputs[0])
                    else:
                        print(f"Error: Unknown state '{inputs[1]}'. Use 'enabled' or 'disabled'.")
                        exit(1)
            else:
                print("Error: Invalid number of arguments.")
                print("Run 'walld set --help' to see usage examples.")
                exit(1)

        elif args.command == "list-active":
            interfaces = proxy.GetActiveInterfaces()
            if interfaces and "ERROR:" not in interfaces[0]:
                print("Active interfaces:")
                for iface, variables in interfaces:
                    print(f"- {iface}")
                    if variables:
                        print("\tVariables:")
                        for var, current_value in variables:
                            print(f"\t- {var} = '{current_value}'")
            elif interfaces:
                result = interfaces[0]
            else:
                print("No active interfaces.")

        if result:
            print(result)

    except DBusError as e:
        print(e)
        exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        exit(1)

if __name__ == "__main__":
    main()

