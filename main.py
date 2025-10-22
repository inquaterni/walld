#!/usr/bin/env python3
import argparse
from dasbus.connection import SessionMessageBus
from config import SERVICE


def fetch_daemon_config_help():
    try:
        bus = SessionMessageBus()
        proxy = bus.get_proxy(SERVICE.interface_name, SERVICE.object_path)
        interfaces = proxy.GetInterfaces()
        variables = proxy.GetVariables()

        help_text = "CONFIG (fetched from daemon):\n"

        if not interfaces and not variables:
            return help_text + "  (Daemon is running, but no dynamic interfaces or variables are configured)\n"

        if interfaces:
            help_text += "  Available Interfaces:\n"
            for iface in interfaces:
                help_text += f"    - {iface}\n"

        if variables:
            if interfaces: help_text += "\n"
            help_text += "  Configurable Variables:\n"
            for var in variables:
                help_text += f"    - {var}\n"

        return help_text

    except Exception:
        return "CONFIG:\n  (Daemon not running. Run daemon to see available interfaces and variables)\n"


def main():
    config_help = fetch_daemon_config_help()

    parser = argparse.ArgumentParser(
        description="Wallpaper Daemon Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{config_help}
Examples:
  %(prog)s --schedule 30 --units m
  %(prog)s --files /path/to/img1.jpg /path/to/img2.png
  %(prog)s --shuffle true
  %(prog)s --activate swww --set-var transition_type grow
  %(prog)s --deactivate hyprpanel
  %(prog)s --next
        """
    )

    parser.add_argument(
        "--schedule",
        type=int,
        metavar="N",
        help="Set schedule interval (requires --units)"
    )

    parser.add_argument(
        "--units",
        choices=["s", "m", "h"],
        help="Time units for schedule: s (seconds), m (minutes), h (hours)"
    )

    parser.add_argument(
        "--files",
        nargs="+",
        metavar="FILE",
        help="Set wallpaper files list"
    )

    parser.add_argument(
        "--shuffle",
        type=str,
        choices=["true", "false", "on", "off", "1", "0", "yes", "no"],
        help="Enable or disable shuffle mode"
    )

    parser.add_argument(
        "--activate",
        nargs="+",
        metavar="IFACE",
        help="Activate one or more interfaces (e.g., swww, hyprpanel)"
    )

    parser.add_argument(
        "--deactivate",
        nargs="+",
        metavar="IFACE",
        help="Deactivate one or more interfaces"
    )

    parser.add_argument(
        "--set-var",
        nargs=2,
        metavar=("KEY", "VALUE"),
        help="Set a dynamic variable (e.g., --set-var transition_type wipe)"
    )

    # parser.add_argument(
    #     "--next",
    #     action="store_true",
    #     help="Change to the next wallpaper"
    # )

    args = parser.parse_args()

    if args.schedule and not args.units:
        parser.error("--schedule requires --units")

    if args.units and not args.schedule:
        parser.error("--units requires --schedule")

    if args.schedule and args.schedule <= 0:
        parser.error("--schedule must be a positive integer")

    try:
        bus = SessionMessageBus()
        proxy = bus.get_proxy(SERVICE.interface_name, SERVICE.object_path)

        results = []

        if args.schedule is not None and args.units is not None:
            result = proxy.SetSchedule(args.schedule, args.units)
            results.append(f"Schedule: {result}")

        if args.files is not None:
            result = proxy.SetFiles(args.files)
            results.append(f"Files: {result}")

        if args.shuffle is not None:
            shuffle_bool = args.shuffle.lower() in ["true", "on", "1", "yes"]
            result = proxy.SetShuffle(shuffle_bool)
            results.append(f"Shuffle: {result}")

        # if args.activate:
        #     # Assumes daemon method is ActivateInterfaces(List[str])
        #     result = proxy.ActivateInterfaces(args.activate)
        #     results.append(f"Activated: {result}")

        # if args.deactivate:
        #     # Assumes daemon method is DeactivateInterfaces(List[str])
        #     result = proxy.DeactivateInterfaces(args.deactivate)
        #     results.append(f"Deactivated: {result}")

        # if args.set_var:
        #     key, value = args.set_var
        #     result = proxy.SetVariable(key, value)
        #     results.append(f"Variable '{key}': {result}")
        # if args.next:
        #     result = proxy.NextWallpaper()
        #     results.append(f"Next Wallpaper: {result}")

        if results:
            for res in results:
                print(res)
        else:
            parser.print_help()

    except Exception as e:
        print(f"Error communicating with service: {e}")


if __name__ == "__main__":
    main()