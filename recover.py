import os
import json
import shutil
import filecmp
import argparse
from pathlib import Path
import urllib.parse
import sys
from datetime import datetime, timedelta

# this took me way too long to figure out, so i hope it saves someone else the headache.🤞 
# basically, vscode keeps a local history of your files in a hidden folder somewhere on your computer. 
# this script digs through that history and tries to pull out the version of your files from right before the big wipe.

# --- config stuff you can change ---

# 1. put the full paths to your projects here as a list.
# windows: [r"C:\Users\name\projects\my-app", r"C:\Users\name\projects\another-app"]
# mac/linux: ["/home/name/projects/my-app", "/home/name/projects/another-app"]
PROJECT_FILTERS = [
    r"YOUR_PROJECT_PATH_HERE",
]

# 2. how far back do you want to go?
# 1 = the absolute latest version (the wiped one)
# 2 = the version before the wipe (usually what you want)
# and so on... just don't go too crazy or you might get a version that doesn't have the file at all. 2 is usually a safe bet.
VERSION_DEPTH = 2

# 3. where do you want the recovered files to go?
# by default, this creates a folder on your desktop. 
# you can change Path.home() / "Desktop" to any path you want. 
# you can also change "VSCode_Recovered_Files" to, perhaps "YOU_ALMOST_GAVE_ME_A_HEART_ATTACK"?
_HOME_DIR = Path.home()
_DESKTOP_DIR = _HOME_DIR / "Desktop"
OUTPUT_DIR = (_DESKTOP_DIR if _DESKTOP_DIR.exists() else _HOME_DIR) / "VSCode_Recovered_Files"

# 4. want the recovered files dumped straight back into the original project?
# set this to True and the script will make a subfolder called
# "vscode_history_recovered" inside each project and put the files there.
# default False keeps everything in the safe OUTPUT_DIR away from your
# live code, which is the sensible default if you just woke up to a wipe.
INPLACE = False

# 5. only recover files that are actually changed or missing?
# True = only bring back files that differ from what's currently in your project,
# or files that are missing entirely (aka added/missing-from-current state).
# False = recover every matching file from local history, even if unchanged.
ONLY_CHANGED_OR_ADDED = True

# 6. want to recover only from a specific time window?
# use local time in 24-hour format: "YYYY-MM-DD HH:MM"
# examples:
# TIME_WINDOW_START = "2026-03-04 09:00"
# TIME_WINDOW_END   = "2026-03-04 18:30"
# set either/both to None to disable that side of the filter.
TIME_WINDOW_START = None
TIME_WINDOW_END = None

# 7. quick preset: recover only the last N hours from now.
# set this to a number like 1, 2, 6, 24, etc.
# this is ignored if TIME_WINDOW_START or TIME_WINDOW_END is set.
HOURS_BACK = None

# ----------------------------------


def _enable_windows_ansi():
    # try to enable VT100/ANSI mode for classic Windows consoles.
    if sys.platform != "win32":
        return True

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        if handle in (0, -1):
            return False

        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) == 0:
            return False

        enable_vt = 0x0004
        if mode.value & enable_vt:
            return True

        return kernel32.SetConsoleMode(handle, mode.value | enable_vt) != 0
    except Exception:
        return False


def _terminal_supports_color():
    if os.getenv("NO_COLOR") is not None:
        return False

    if os.getenv("CLICOLOR_FORCE") == "1":
        return True

    if os.getenv("TERM", "").lower() == "dumb":
        return False

    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False

    if sys.platform == "win32":
        # modern terminals often support ANSI out of the box.
        if os.getenv("WT_SESSION") or os.getenv("ANSICON") or os.getenv("ConEmuANSI") == "ON":
            return True
        return _enable_windows_ansi()

    return True


_USE_COLOR = _terminal_supports_color()

_CLR_RESET = "\033[0m"
_CLR_GREEN = "\033[92m"
_CLR_YELLOW = "\033[93m"
_CLR_RED = "\033[91m"
_CLR_CYAN = "\033[96m"
_CLR_MAGENTA = "\033[95m"


def _paint(text, color):
    if not _USE_COLOR:
        return text
    return f"{color}{text}{_CLR_RESET}"


def _success(text):
    print(_paint(text, _CLR_GREEN))


def _warn(text):
    print(_paint(text, _CLR_YELLOW))


def _error(text):
    print(_paint(text, _CLR_RED))


def _info(text):
    print(_paint(text, _CLR_CYAN))


def _accent(text):
    print(_paint(text, _CLR_MAGENTA))

def _parse_time_window(value, label):
    if value is None:
        return None

    value = value.strip()
    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        _error(f"heads up: '{label}' has a bad format: '{value}'")
        _info("use this format: YYYY-MM-DD HH:MM (example: 2026-03-04 14:30)")
        sys.exit(1)


def _build_arg_parser():
    # quick terminal flags so you don't have to keep editing the file config
    parser = argparse.ArgumentParser(
        description="Recover files from VS Code Local History."
    )
    parser.add_argument(
        "-p",
        "--project",
        action="append",
        default=None,
        help="Project path to recover. Repeat for multiple projects.",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=None,
        help="Version depth to recover (default from config).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for recovered files (ignored when --inplace is used).",
    )
    parser.add_argument(
        "--inplace",
        action="store_true",
        help="Recover into <project>/vscode_history_recovered.",
    )
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Recover all files, even unchanged ones.",
    )
    parser.add_argument(
        "--start",
        default=None,
        help='Time window start in "YYYY-MM-DD HH:MM".',
    )
    parser.add_argument(
        "--end",
        default=None,
        help='Time window end in "YYYY-MM-DD HH:MM".',
    )
    parser.add_argument(
        "--hours-back",
        type=float,
        default=None,
        help="Recover entries from the last N hours.",
    )
    return parser


def _clean_project_filters(project_filters):
    # drop empty placeholders so the script doesn't try to resolve ""
    cleaned = []
    for path in project_filters:
        if path and path.strip():
            cleaned.append(path)
    return cleaned


def _resolve_runtime_settings(args):
    project_filters = args.project if args.project else PROJECT_FILTERS
    project_filters = _clean_project_filters(project_filters)

    if not project_filters:
        _error("heads up: no project paths were provided.")
        _info("set PROJECT_FILTERS in the file or pass --project in terminal.")
        sys.exit(1)

    version_depth = args.depth if args.depth is not None else VERSION_DEPTH
    if version_depth <= 0:
        _error("quick fix: version depth must be 1 or higher 😅")
        sys.exit(1)

    output_dir = Path(args.output_dir).expanduser() if args.output_dir else OUTPUT_DIR

    # if flag is passed, force inplace on. otherwise use config value.
    inplace = True if args.inplace else INPLACE

    # if --all-files is passed, disable changed-only behavior.
    only_changed_or_added = False if args.all_files else ONLY_CHANGED_OR_ADDED

    # explicit CLI start/end beats config start/end.
    time_window_start = args.start if args.start is not None else TIME_WINDOW_START
    time_window_end = args.end if args.end is not None else TIME_WINDOW_END

    # explicit CLI --hours-back beats config HOURS_BACK.
    hours_back = args.hours_back if args.hours_back is not None else HOURS_BACK

    return {
        "project_filters": project_filters,
        "version_depth": version_depth,
        "output_dir": output_dir,
        "inplace": inplace,
        "only_changed_or_added": only_changed_or_added,
        "time_window_start": time_window_start,
        "time_window_end": time_window_end,
        "hours_back": hours_back,
    }


def _find_history_path():
    # try a few common app names so this works for VS Code + Insiders + VSCodium
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA")
        if not appdata:
            return None, []

        base = Path(appdata)
        candidates = [
            base / "Code" / "User" / "History",
            base / "Code - Insiders" / "User" / "History",
            base / "VSCodium" / "User" / "History",
            base / "Code - OSS" / "User" / "History",
        ]
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
        candidates = [
            base / "Code" / "User" / "History",
            base / "Code - Insiders" / "User" / "History",
            base / "VSCodium" / "User" / "History",
            base / "Code - OSS" / "User" / "History",
        ]
    else:
        xdg_config = os.getenv("XDG_CONFIG_HOME")
        base = Path(xdg_config) if xdg_config else Path.home() / ".config"
        candidates = [
            base / "Code" / "User" / "History",
            base / "Code - Insiders" / "User" / "History",
            base / "VSCodium" / "User" / "History",
            base / "Code - OSS" / "User" / "History",
        ]

    for candidate in candidates:
        if candidate.exists():
            return candidate, candidates

    return None, candidates


def _resource_uri_to_path(original_uri):
    parsed_uri = urllib.parse.urlparse(original_uri)
    if parsed_uri.scheme and parsed_uri.scheme != "file":
        return None

    raw_path = urllib.parse.unquote(parsed_uri.path)

    # keep network share support alive (mostly for windows/UNC paths)
    if parsed_uri.netloc and parsed_uri.netloc not in ("", "localhost"):
        raw_path = f"//{parsed_uri.netloc}{raw_path}"

    # fix windows drive slash issue (/C:/foo -> C:/foo)
    if sys.platform == "win32" and raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        raw_path = raw_path[1:]

    if not raw_path:
        return None

    return Path(raw_path).resolve()


def recover(runtime):
    project_filters = runtime["project_filters"]
    version_depth = runtime["version_depth"]
    output_dir = runtime["output_dir"]
    inplace = runtime["inplace"]
    only_changed_or_added = runtime["only_changed_or_added"]
    time_window_start = runtime["time_window_start"]
    time_window_end = runtime["time_window_end"]
    hours_back = runtime["hours_back"]

    history_path, history_candidates = _find_history_path()

    # just a quick check to make sure the history folder exists before we do anything
    if not history_path:
        _error("man, i can't find a VS Code history folder 😩")
        _warn("i checked these paths:")
        for candidate in history_candidates:
            print(f"  - {candidate}")
        return

    if not output_dir.exists():
        output_dir.mkdir(parents=True)

    start_dt = _parse_time_window(time_window_start, "TIME_WINDOW_START")
    end_dt = _parse_time_window(time_window_end, "TIME_WINDOW_END")

    # quick mode: if explicit window isn't set, use the "last N hours" preset
    if not start_dt and not end_dt and hours_back is not None:
        try:
            hours_back = float(hours_back)
        except (TypeError, ValueError):
            _error(f"quick fix: HOURS_BACK must be a number. got: {hours_back}")
            sys.exit(1)

        if hours_back <= 0:
            _error("quick fix: HOURS_BACK needs to be greater than 0 😅")
            sys.exit(1)

        end_dt = datetime.now()
        start_dt = end_dt - timedelta(hours=hours_back)

    if start_dt and end_dt and start_dt > end_dt:
        _error("quick fix: TIME_WINDOW_START can't be after TIME_WINDOW_END 😅")
        sys.exit(1)

    if start_dt or end_dt:
        _info("time filter is ON. only recovering snapshots in this window:")
        _accent(f"  start: {start_dt if start_dt else 'no lower limit'}")
        _accent(f"  end  : {end_dt if end_dt else 'no upper limit'}")

    total_count = 0
    total_skipped_unchanged = 0
    total_skipped_outside_window = 0

    for project_filter in project_filters:
        filter_path = Path(project_filter).resolve()
        project_name = filter_path.name  # we're using the folder name as the subfolder name

        # decide where to dump the recovered files
        if inplace:
            # if we're doing inplace recovery, put them in a special folder
            project_output_dir = filter_path / "vscode_history_recovered"
        else:
            project_output_dir = output_dir / project_name

        # if the destination exists, ask before stomping on it
        if project_output_dir.exists():
            _warn(f"\n🔥 whoa, slow down! '{project_output_dir}' already exists.")
            _warn("   if we keep going we'll stomp whatever's in there.")
            print()  # just a little spacing for readability
            _warn("   type 'yes' to overwrite, 'no' to skip this project, or 'nuke' to delete the whole folder and start fresh.")
            print()  # more spacing for readability
            answer = input("   what's your pick? (yes / no / nuke): ").strip().lower()
            
            if answer == "nuke":
                # user (which is probably you) wants to blow the whole folder away and start fresh
                shutil.rmtree(project_output_dir)
                project_output_dir.mkdir(parents=True)
            elif answer != "yes":
                _warn(f"   OK, skipping {project_name} this round.")
                continue

        if not project_output_dir.exists():
            project_output_dir.mkdir(parents=True)

        count = 0
        skipped_unchanged = 0
        skipped_outside_window = 0

        # vscode stores history in folders with random hex names
        for folder in history_path.iterdir():
            if not folder.is_dir(): continue

            entries_file = folder / 'entries.json'
            if not entries_file.exists(): continue

            try:
                with open(entries_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    original_uri = data.get('resource')
                    entries = data.get('entries', [])

                    if not original_uri or not entries: continue

                    # convert vscode uri thing into a normal file path
                    original_file_path = _resource_uri_to_path(original_uri)
                    if original_file_path is None:
                        continue

                    # only grab it if it lives inside the project we care about
                    try:
                        relative_to_project = original_file_path.relative_to(filter_path)
                    except ValueError:
                        continue

                    # sort by time just in case
                    entries.sort(key=lambda x: x['timestamp'])

                    # if user gave a time window, keep only entries inside it
                    entries_in_window = entries
                    if start_dt or end_dt:
                        entries_in_window = []
                        for entry in entries:
                            ts = entry.get('timestamp')
                            if ts is None:
                                continue

                            entry_dt = datetime.fromtimestamp(ts / 1000)
                            if start_dt and entry_dt < start_dt:
                                continue
                            if end_dt and entry_dt > end_dt:
                                continue

                            entries_in_window.append(entry)

                        if not entries_in_window:
                            skipped_outside_window += 1
                            continue

                    # grab the version the user requested
                    if len(entries_in_window) >= version_depth:
                        target_entry = entries_in_window[-version_depth]
                        label = f"version -{version_depth}"
                    else:
                        target_entry = entries_in_window[0]
                        label = "oldest available"

                    source_file = folder / target_entry['id']

                    if source_file.exists():
                        # only recover files that are changed/missing if that mode is enabled
                        current_project_file = filter_path / relative_to_project
                        if only_changed_or_added and current_project_file.exists():
                            try:
                                if filecmp.cmp(source_file, current_project_file, shallow=False):
                                    _warn(f"unchanged, skipped: {relative_to_project}")
                                    skipped_unchanged += 1
                                    continue
                            except OSError:
                                # if comparison fails for any weird reason, treat it as changed and recover it
                                pass

                        # copy it and keep the folder structure
                        target_file = project_output_dir / relative_to_project
                        target_file.parent.mkdir(parents=True, exist_ok=True)

                        shutil.copy2(source_file, target_file)
                        _success(f"restored ({label}): {relative_to_project}")
                        count += 1

            except:
                # skip if file is busy or weird
                pass

        # just a little message after each project is done so you can see the progress
        _success(
            f"\nall done for project '{project_name}'. recovered {count} files"
            f" (skipped {skipped_unchanged} unchanged, {skipped_outside_window} outside time window)"
            f" to: {project_output_dir} 🎉"
        )
        total_count += count
        total_skipped_unchanged += skipped_unchanged
        total_skipped_outside_window += skipped_outside_window

    # final message after all projects are done
    _success(
        f"\nall done. recovered a total of {total_count} files across all projects"
        f" (skipped {total_skipped_unchanged} unchanged, {total_skipped_outside_window} outside time window)"
        f" to: {output_dir} 🎉"
    )

if __name__ == "__main__":
    parser = _build_arg_parser()
    args = parser.parse_args()
    runtime = _resolve_runtime_settings(args)

    _info("\n" + "="*60)
    _accent("   🛠️  VSCode Local History Recovery Tool  🛠️")
    _info("" + "="*60 + "\n")
    _info("Hey there. I’m going to dig through VSCode’s secret stash and bring your lost files back to life.")
    _info("Be patient, maybe grab a coffee, or just stare at the terminal like it’s a sci-fi movie. 🍿\n")
    recover(runtime)