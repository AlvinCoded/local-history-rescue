# vscode-local-history-rescue

So, I was using a VS Code AI agent and it suggested a command to "fix" my workspace. I was moving fast, overlooked what it actually said, and hit enter. Turns out, it ran `git restore .` on a massive chunk of files I hadn't committed yet. 

Technically it was my fault for not reading the prompt closely, but man, it hurt. All my work was wiped and Git had no record of it because I hadn't staged the changes.😭

I looked everywhere for a way to bulk-restore my files using VS Code's built-in "Local History," but apparently, the only way to do it is to click every single file in the UI and manually restore it. If you have 50 files, you're looking at a very bad afternoon.

I wrote this script to solve that. It reaches into the hidden folders where VS Code stores its "Timeline" snapshots and pulls your code back from the dead.🙌

## What this does
It digs into the internal VS Code history folders, filters for files belonging to your specific project, and pulls out the version saved right before the "big wipe."

It auto-detects common history locations for VS Code variants (`Code`, `Code - Insiders`, `VSCodium`, and `Code - OSS`) on Windows, macOS, and Linux.

## Prerequisites
- Just **Python 3**. No extra libraries to install or anything like that.
- Works on **Windows**, **Linux**, and **macOS**.

## How to use it
1. Download `recover.py`.
2. Open it and edit the top section:
   - `PROJECT_FILTERS`: A list of full paths to your messed-up projects. Yes, you can recover multiple projects at once. Just add them to the list, and the script will handle the rest.
   - `VERSION_DEPTH`: Set to `2` to get the version just before the most recent save. You can set it higher if you want to go further back in time, but usually `2` is what you want for a recent accident.
   - `OUTPUT_DIR`: Where you want the recovered files to go. The script will create subfolders for each project automatically, so your recovered files stay organized.
   - `ONLY_CHANGED_OR_ADDED`: Default is `True`, which means it only restores files that are actually different from your current project (or missing entirely). This keeps the recovery output clean.
   - `TIME_WINDOW_START` / `TIME_WINDOW_END`: Optional local time window (`YYYY-MM-DD HH:MM`) so you only recover snapshots from a specific time range. Great when you only want files touched around the exact "uh oh" moment.
   - `HOURS_BACK`: Quick preset for "recover from the last N hours" (example: `HOURS_BACK = 2`). Nice when you don't want to type exact timestamps.

3. Run it in your terminal: `python recover.py`.
   - If the script detects that a project folder already exists in the output directory, it will warn you and offer three choices:
     * `yes` – write the new files on top of whatever’s already there (existing files with the same names will be replaced).
     * `no` – skip this project entirely for now.
     * `nuke` – delete the whole recovery folder and start fresh before copying. Handy when you’ve already inspected the previous run and want a clean slate.
   That way you don’t accidentally clobber things without knowing what’s happening.

4. Go to your output folder and your code should be there, folder structure and all.

## Where do the files end up?
By default, everything lands in the `OUTPUT_DIR` you configured, under a subfolder for each project. That's the safe mode – your original code stays untouched while you poke through what got recovered.

If you'd rather have the recovered bits dropped directly into the project itself, set `INPLACE = True` at the top of `recover.py`. When enabled the script will create a `vscode_history_recovered` _(feel free to rename it)_ folder at the root of each project and dump the files there. It'll still prompt you before overwriting an existing recovery folder, because accidents.

> Note: `in‑place` recovery is opt‑in for a reason – it can overwrite existing files if you're **not** careful, and some people like to inspect the output in a separate location first.

By default it also skips unchanged files and tells you how many were skipped in the summary. If you want the "recover everything no matter what" behavior, set `ONLY_CHANGED_OR_ADDED = False`.

If you set a time window, `VERSION_DEPTH` is applied *inside that window* (not across all history ever). Example: if the window is from `2026-03-04 09:00` to `2026-03-04 10:00`, the script only picks versions from that hour.

Important behavior: if the same file has many edits inside the selected window, the script still restores that file only once per run. It picks one version based on `VERSION_DEPTH` (`1` = latest in window, `2` = one before latest, etc.).

`HOURS_BACK` is only used when both `TIME_WINDOW_START` and `TIME_WINDOW_END` are `None`. If you set explicit start/end values, those win.

## Terminal Flags (No file edits needed)

You can also pass options directly in terminal and they override config values for that run.

Basic run with one project:
`python recover.py --project "C:\\Users\\you\\Desktop\\my-project"`

Multiple projects:
`python recover.py --project "C:\\proj-a" --project "C:\\proj-b"`

Last 2 hours only:
`python recover.py --project "C:\\proj-a" --hours-back 2`

Exact time window:
`python recover.py --project "C:\\proj-a" --start "2026-03-05 09:00" --end "2026-03-05 11:30"`

Recover all files (even unchanged ones):
`python recover.py --project "C:\\proj-a" --all-files`

In-place recovery folder inside each project:
`python recover.py --project "C:\\proj-a" --inplace`

Custom output dir + depth:
`python recover.py --project "C:\\proj-a" --output-dir "D:\\Recovery" --depth 2`

Flag notes:
- `--project` can be repeated.
- `--hours-back` only applies when `--start/--end` are not provided.
- `--all-files` disables changed-only filtering for that run.

## Terminal Experience

When you run the script, it greets you with a friendly message and keeps you updated on its progress. It’s like having a buddy cheer you on while you recover your files.

If you’re recovering multiple projects, the script will let you know how many files it recovered for each project and where they’re saved. No guesswork, no stress. 🍿

## Installation Guide for Python (if you don't have it yet)

No worries if Python isn't on your system yet. Here's a guide to get you started:

1. **Download Python**:
   - Head over to the [official Python downloads page](https://www.python.org/downloads/).
   - Click the big, friendly "Download Python" button. It'll automatically detect your operating system (Windows, macOS, or Linux).

2. **Install Python**:
   - Run the installer you just downloaded.
   - On Windows, make sure to check the box that says "Add Python to PATH" before clicking "Install Now." This makes your life easier later.
   - On macOS and Linux, follow the prompts. It's usually straightforward.

3. **Verify the installation**:
   - Open a terminal (Command Prompt, PowerShell, or your favorite terminal app).
   - Type `python --version` and hit Enter. You should see something like `Python 3.x.x`.

4. **You're good to go!**:
   - Now you can run the `recover.py` script and rescue your files like a pro. 🙌

> If you hit any snags, the Python website has a [Beginner's Guide](https://wiki.python.org/moin/BeginnersGuide) to help you out. Or just Google it, and you'll find tons of tutorials and videos.

_Hope this saves you the hours of work it saved me!_ 😁

~ Best, Alvin.