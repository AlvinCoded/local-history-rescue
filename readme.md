# local-history-rescue

What this does is, it digs into the internal VS Code history folders, filters for files belonging to your specific project, and pulls out the version saved right before the "big wipe."

It auto-detects common history locations for VS Code variants (`Code`, `Code - Insiders`, `VSCodium`, and `Code - OSS`) across platforms.

## What is needed
- Just **Python 3**. No extra libraries to install or anything like that.
- Works on **Windows**, **Linux**, and **macOS**.

## How to use it
1. Download `smart_recover.py`.
2. Open it and edit the top section:
   - `PROJECT_FILTERS`: A list of full paths to your messed-up projects. Yes, you can recover multiple projects at once. Just add them to the list, and the script will handle the rest.
   - `OUTPUT_DIR`: Where you want the recovered files to go. The script will create subfolders for each project automatically, so your recovered files stay organized.
   - `ONLY_CHANGED_OR_ADDED`: Default is `True`, which means it only restores files that are actually different from your current project (or missing entirely). This keeps the recovery output clean.
   - `TIME_WINDOW_START` / `TIME_WINDOW_END`: Optional local time window (`YYYY-MM-DD HH:MM`) so you only recover snapshots from a specific time range. Great when you only want files touched around the exact "uh oh" moment.
   - `HOURS_BACK`: Quick preset for "recover from the last N hours" (example: `HOURS_BACK = 2`). Nice when you don't want to type exact timestamps.

3. Run it in your terminal: `python smart_recover.py`.
   - If the script detects that a project folder already exists in the output directory, it will warn you and offer three choices:
     * `yes` – write the new files on top of whatever’s already there (existing files with the same names will be replaced).
     * `no` – skip this project entirely for now.
     * `nuke` – delete the whole recovery folder and start fresh before copying. Handy when you’ve already inspected the previous run and want a clean slate.
   That way you don’t accidentally clobber things without knowing what’s happening.

4. Go to your output folder and your code should be there, folder structure and all.

## Core Commands
Basic run:

```bash
python smart_recover.py --project "C:\\Users\\you\\Desktop\\my-project"
```

Multiple projects:

```bash
python smart_recover.py --project "C:\\proj-a" --project "C:\\proj-b"
```

Last 2 hours only:

```bash
python smart_recover.py --project "C:\\proj-a" --hours-back 2
```

Exact time window:

```bash
python smart_recover.py --project "C:\\proj-a" --start "2026-03-05 09:00" --end "2026-03-05 11:30"
```

Recover all files (including unchanged):

```bash
python smart_recover.py --project "C:\\proj-a" --all-files
```

Recover inside project (`vscode_history_recovered_ml`):

```bash
python smart_recover.py --project "C:\\proj-a" --inplace
```

Custom output folder:

```bash
python smart_recover.py --project "C:\\proj-a" --output-dir "D:\\Recovery"
```

Force a clean run (ignore resume checkpoint):

```bash
python smart_recover.py --project "C:\\proj-a" --label-interactive --fresh-run
```

## Smart Modes (Offline & Privacy-first)
This script can rank multiple candidates per file and learn from your feedback over time.

Interactive review + online learning:

```bash
python smart_recover.py --project "C:\\Users\\you\\Desktop\\my-project" --label-interactive
```
> In this mode, the script will show you each candidate file it finds and ask you to label it as "keep" or "discard." Your feedback is used to update the model's understanding of what good recoveries look like, so it gets smarter with each run.

Only ask when uncertain:

```bash
python smart_recover.py --project "C:\\Users\\you\\Desktop\\my-project" --label-interactive --interactive-uncertain-only --confidence-threshold 0.70
```
> In this mode, the script will only prompt you for files where the model's confidence is below 70%. For files above that threshold, it will automatically recover them without asking. This way, you can focus your attention on the files that need it most.

Autonomous mode with safety gates:

```bash
python smart_recover.py --project "C:\\Users\\you\\Desktop\\my-project" --autonomous
```
> In autonomous mode, the script will automatically recover files it is confident about without asking for your input. However, it will only recover files where the confidence is above a default threshold (e.g., 0.80) and will limit the number of files recovered in one run (e.g., 50) to prevent any accidental mass recoveries. You can adjust these parameters with `--autonomous-min-confidence` and `--autonomous-max-files` like can be seen in the next example.

Stricter autonomous mode:

```bash
python smart_recover.py --project "C:\\Users\\you\\Desktop\\my-project" --autonomous --autonomous-min-confidence 0.92 --autonomous-max-files 80
```

If you quit in the middle, the script now saves progress and resumes from where it left off when you run again with the same project + mode.

## Train From Labels
After a run, the script writes:
- `~/.vscode_history_rescue_ml/model.json`
- `~/.vscode_history_rescue_ml/last_manifest.json`

To train with your own labels:
1. Open `last_manifest.json`
2. Create `labels.json` like:

```json
{"src/file.py": "candidate_id_here"}
```

3. Train:
`python smart_recover.py --train --labels labels.json`

Resume state is stored at:
- `~/.vscode_history_rescue_ml/run_state.json`

Resume is matched by project scope, mode (`--label-interactive` vs `--autonomous`), and destination safety settings (`--inplace` / output dir).
Use `--fresh-run` anytime you want to deliberately ignore checkpoint state.

**Remember:**
- This is intentionally lightweight and fully offline.
- It is not an LLM; it is an incremental ranking model tuned for this recovery task.
- Under the hood it now uses SGD-style online updates with regularization, gradient clipping, and averaged weights for better stability.
- Drift signal is computed from recent feedback accuracy and used to adapt query behavior.
- Contextual bandit arm rewards update only when explicit feedback exists (manual review decisions).

**Some references used while building this:**
- Multi-armed bandit overview (exploration vs exploitation, contextual variants): https://en.wikipedia.org/wiki/Multi-armed_bandit
- Chu, Li, Reyzin, Schapire (AISTATS 2011), *Contextual Bandits with Linear Payoff Functions*: https://proceedings.mlr.press/v15/chu11a.html
- Li, Chu, Langford, Schapire (WWW 2010), *A Contextual-Bandit Approach to Personalized News Article Recommendation*: https://arxiv.org/abs/1003.0146

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
   - Now you can run the `smart_recover.py` script and rescue your files like a pro. 🙌

> If you hit any snags, the Python website has a [Beginner's Guide](https://wiki.python.org/moin/BeginnersGuide) to help you out. Or just Google it, and you'll find tons of tutorials and videos.

_Hope this saves you the hours of work it saved me!_ 😁

~ Best, Alvin.
