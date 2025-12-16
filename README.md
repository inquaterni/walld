## WallD – Wallpaper Rotation Daemon

WallD is a D‑Bus wallpaper rotation daemon with a **backend‑agnostic interface model**.  
The core daemon never talks directly to a specific compositor or desktop; instead it runs user‑defined commands (interfaces) configured in TOML, so it can drive any wallpaper backend that can be controlled from the shell.

### Features

- **Daemon over D‑Bus**: Exposes a `com.walld.WallDaemon` interface on the session bus.
- **Backend‑agnostic interfaces**: Any command‑line wallpaper setter can be wired in via config.
- **Config‑driven**: Single TOML config controls schedule, shuffle, image source, and interfaces.
- **Async wallpaper updates**: Uses GLib / Gio to run wallpaper commands off the main loop.
- **CLI client**: `walld` command to inspect and control the running daemon.

### Requirements

- Python 3.11
- `dasbus`
- `PyGObject`

On Arch‑like systems, you’ll typically need:

- `python-dasbus`
- `python-gobject` (or equivalent)

### Installation

Clone the repo:

```bash
git clone https://github.com/inquaterni/walld
cd walld
```

Set up a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### Install via `pyproject.toml`

With the virtual environment activated, install the package (editable mode is convenient for development):

```bash
pip install -e .
```
Or just
```bash
pip install .
```
This will install the console scripts defined in `pyproject.toml`:
- `walld` – CLI client
- `walld-server` – daemon entry point
You can then run:
```bash
walld-server
```

Or, if you prefer, you can still run the daemon directly:
```bash
python server.py
```

By default, the daemon expects to find config.toml in your `$HOME/.config/walld/config.toml`.
To copy default config from repo you can run:

```bash
mkdir -p ~/.config/walld
cp ./default.toml ~/.config/walld/config.toml
```

### Configuration
WallD reads a single TOML file and builds its runtime configuration via `toml_config.ConfigBuilder`.  
Two top‑level tables are relevant:

- **`[Daemon]`**: global daemon behaviour
- **`[Interfaces]`**: backend‑agnostic wallpaper setters

Minimal example:

```toml
[Daemon]
schedule = 10          # integer; rotation interval
units = "m"            # "s" / "m" / "h"
shuffle = true         # randomize wallpaper order
path = "~/Pictures/wallpapers"
active_interfaces = ["swaybg"]

[Interfaces]
# Simple interface: list of args becomes the command.
swaybg = ["swaybg", "-i", "%f", "--mode", "fill"]
```

> [!NOTE]
> `path` cannot lead to single file. Use directory path instead.

The daemon walks the `path` directory and automatically picks all files that look like images.  
When it’s time to change the wallpaper, it runs each **active interface** with the selected filename substituted into the argument list.

### Interfaces and Variables

Interfaces are declared under the `[Interfaces]` table:

- **Simple form**: `name = [ "cmd", "arg1", "%f", ... ]`
  - `%f` is replaced with the current wallpaper file path.
- **Verbose form**: `name = { args = [...], variables = { ... } }`

Verbose example:

```toml
[Interfaces.swww]
args = ["swww", "img", "%f", "--transition-fps", "%fps", "--transition-type", "%type"]

[Interfaces.swww.variables]
fps  = { value = 60 }
type = { current = "wipe", options = ["grow", "wipe", "fade"] }
```

At runtime, each argument beginning with `%` (other than `%f`) is treated as a variable name and resolved via the interface’s variable map (`Mutable`, `Constant`, or `Enumeration`), allowing you to reuse the same command template with different values.  
This is how WallD stays backend‑agnostic: the daemon only knows about interfaces and variables, not specific compositors.

### Running the Daemon

From the repo root:

```bash
python server.py
```
Or, if installed, simply:

```bash
walld-server
```

This will:

- Parse the configured TOML.
- Register `com.walld.WallDaemon` on the session bus.
- Start a GLib event loop and schedule wallpaper updates.

Logs are sent to syslog (`/dev/log`, facility `LOCAL1`) using the `SysLogHandler`.

> [!WARNING]
> **Do _not_ run `server.py` or `walld-server` as root!**
> This script executes generic commands, so running it with elevated privileges could allow unintended or malicious actions to compromise your system.

### CLI Usage

`main.py` implements a command‑line client talking to the D‑Bus service. You can run it directly:

```bash
python main.py <subcommand> [...]
```

Or, if installed, simply:

```bash
walld <subcommand> [...]
```

Available subcommands:

- **`schedule <value> <s|m|h>`**: set rotation interval.
- **`files <file1> [file2 ...]`**: replace the current wallpaper list with explicit file paths.
- **`shuffle <on|off>`**: toggle shuffle.
- **`current-wallpaper`**: print filename of the current wallpaper.
- **`list`**: list all configured interfaces and their non‑constant variables.
- **`set ...`**: update interface variables or enable/disable interfaces:
  - `walld set <iface> <var> <value>`
  - `walld set <iface>.<var> <value>`
  - `walld set <iface> enabled|disabled`
- **`list-active`**: list only active interfaces.


Examples:

```bash
walld schedule 30 m
walld shuffle on
walld files ~/Pictures/wallpapers/a.jpg ~/Pictures/wallpapers/b.jpg
walld set swaybg enabled
```

### Error Handling

The daemon raises specific error types (e.g. `InvalidInterfaceNameError`, `NoFilesProvidedError`, `UnknownTimeUnitsError`, `VariableTypeError`) which are translated to D‑Bus errors.  
The CLI prints D‑Bus `DBusError` messages and suggests checking whether the server is running if it can’t reach the service.

### Design Notes

- **Backend‑agnostic core**: the daemon only knows “interfaces”, so adding support for a new compositor or wallpaper tool is just a matter of editing the TOML.
- **Strong typing around config**: `Config`, `Interface`, `Var` types, and enum `Units` ensure config is validated before use. Runtime type checking validates variable assignments and raises `VariableTypeError` when types don't match.
- **Non‑blocking updates**: wallpaper setting work is dispatched through `Gio.Task` so the D‑Bus service stays responsive during image changes.