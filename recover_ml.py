import argparse
import filecmp
import json
import math
import os
import random
import shutil
import sys
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path


# same vibe, new brain.
# this script is an experimental offline ML version of the recovery flow.
# it learns how to pick a better snapshot over time using your feedback.
# is it worth it? who knows! but it was a fun experiment to build and maybe you'll find it useful too.😁


# --- config stuff you can change ---

# 1. put the full paths to your projects here as a list.
# windows: [r"C:\Users\name\projects\my-app", r"C:\Users\name\projects\another-app"]
# mac/linux: ["/home/name/projects/my-app", "/home/name/projects/another-app"]
PROJECT_FILTERS = [
    r"YOUR_PROJECT_PATH_HERE",
]

# 2. where do you want the recovered files to go?
# by default, this creates a folder on your desktop. 
# you can change Path.home() / "Desktop" to any path you want. 
# you can also change "VSCode_Recovered_Files" to, perhaps "YOU_ALMOST_GAVE_ME_A_HEART_ATTACK"?
_HOME_DIR = Path.home()
_DESKTOP_DIR = _HOME_DIR / "Desktop"
OUTPUT_DIR = (_DESKTOP_DIR if _DESKTOP_DIR.exists() else _HOME_DIR) / "VSCode_Recovered_Files"

# 3. want the recovered files dumped straight back into the original project?
# set this to True and the script will make a subfolder called
# "vscode_history_recovered_ml" inside each project and put the files there.
# default False keeps everything in the safe OUTPUT_DIR away from your
# live code, which is the sensible default if you just woke up to a wipe.
INPLACE = False

# 4. only recover files that are actually changed or missing?
# True = only bring back files that differ from what's currently in your project,
# or files that are missing entirely (aka added/missing-from-current state).
# False = recover every matching file from local history, even if unchanged.
ONLY_CHANGED_OR_ADDED = True

# 5. want to recover only from a specific time window?
# use local time in 24-hour format: "YYYY-MM-DD HH:MM"
# examples:
# TIME_WINDOW_START = "2026-03-04 09:00"
# TIME_WINDOW_END   = "2026-03-04 18:30"
# set either/both to None to disable that side of the filter.
TIME_WINDOW_START = None
TIME_WINDOW_END = None

# 6. quick preset: recover only the last N hours from now.
# set this to a number like 1, 2, 6, 24, etc.
# this is ignored if TIME_WINDOW_START or TIME_WINDOW_END is set.
HOURS_BACK = None

# 7. ML storage paths (all local, all offline).
# model.json keeps learned weights/state.
# last_manifest.json keeps candidate choices and run metadata.
# if these files disappear, the model just starts fresh and keeps going.
MODEL_STATE_DIR = Path.home() / ".vscode_history_rescue_ml"
MODEL_STATE_FILE = MODEL_STATE_DIR / "model.json"
MANIFEST_FILE = MODEL_STATE_DIR / "last_manifest.json"

# 8. learning knobs.
# defaults are intentionally lightweight so updates stay stable.
LEARNING_RATE = 0.08
MARGIN = 0.05
L2_REG = 0.0005
LR_DECAY = 0.0002
GRAD_CLIP = 2.5
DEFAULT_TEMPERATURE = 1.0

# 9. drift + autonomy knobs.
# these control when the script asks for human review vs auto-accepts.
DRIFT_WINDOW = 40
DRIFT_MIN_SAMPLES = 8
DRIFT_ALERT_LEVEL = 0.20
AUTONOMOUS_MIN_CONF = 0.88
AUTONOMOUS_MAX_FILES = 120

DEFAULT_WEIGHTS = {
    "bias": 0.0,
    "rank_latest": 1.2,
    "recency_hours": -0.04,
    "similarity_to_current": -0.35,
    "size_delta_ratio": -0.30,
    "is_missing_in_current": 0.7,
}

# 10. bandit "arms" choose ranking styles on the fly.
BANDIT_ARMS = [
    "balanced",
    "recency_boost",
    "similarity_boost",
    "missing_boost",
]
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


def _build_arg_parser():
    # keep CLI flexible so you can run quick recoveries or full feedback loops.
    parser = argparse.ArgumentParser(
        description="Recover files from VS Code Local History with an offline learning model."
    )
    parser.add_argument("--project", action="append", default=None, help="Project path. Repeat for multiple projects.")
    parser.add_argument("--output-dir", default=None, help="Output directory (ignored when --inplace is used).")
    parser.add_argument("--inplace", action="store_true", help="Recover into <project>/vscode_history_recovered_ml.")
    parser.add_argument("--all-files", action="store_true", help="Recover all files, even unchanged ones.")
    parser.add_argument("--start", default=None, help='Time window start: "YYYY-MM-DD HH:MM"')
    parser.add_argument("--end", default=None, help='Time window end: "YYYY-MM-DD HH:MM"')
    parser.add_argument("--hours-back", type=float, default=None, help="Recover entries from last N hours.")

    # interactive active-learning mode
    parser.add_argument(
        "--label-interactive",
        action="store_true",
        help="Ask approve/reject in terminal and train online during recovery.",
    )
    parser.add_argument(
        "--interactive-top-k",
        type=int,
        default=5,
        help="How many top candidates to show during interactive review.",
    )
    parser.add_argument(
        "--interactive-uncertain-only",
        action="store_true",
        help="Only ask for labels on uncertain picks (active learning).",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=0.70,
        help="Base confidence threshold for uncertainty checks (adapted at runtime by drift).",
    )

    # autonomous mode with safety gates
    parser.add_argument(
        "--autonomous",
        action="store_true",
        help="Auto-accept high confidence picks and only ask when safety gates trigger.",
    )
    parser.add_argument(
        "--autonomous-min-confidence",
        type=float,
        default=AUTONOMOUS_MIN_CONF,
        help="Minimum confidence required for unattended auto-accept.",
    )
    parser.add_argument(
        "--autonomous-max-files",
        type=int,
        default=AUTONOMOUS_MAX_FILES,
        help="Max files to auto-accept before forcing manual review prompts.",
    )

    # batch training mode
    parser.add_argument("--train", action="store_true", help="Train model using a manifest + labels JSON.")
    parser.add_argument("--manifest", default=None, help="Manifest path (default: last manifest).")
    parser.add_argument("--labels", default=None, help="Labels JSON path for training.")

    return parser


def _clean_project_filters(project_filters):
    # drop empty placeholders so the script doesn't try to resolve ""
    cleaned = []
    for path in project_filters:
        if path and path.strip():
            cleaned.append(path)
    return cleaned


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


def _resolve_runtime_settings(args):
    # CLI flags override in-file config for this run only.
    project_filters = args.project if args.project else PROJECT_FILTERS
    project_filters = _clean_project_filters(project_filters)

    if not args.train and not project_filters:
        _error("heads up: no project paths were provided.")
        _info("set PROJECT_FILTERS in the file or pass --project in terminal.")
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

    top_k = args.interactive_top_k
    if top_k <= 0:
        _error("quick fix: --interactive-top-k must be 1 or higher")
        sys.exit(1)

    confidence_threshold = args.confidence_threshold
    if confidence_threshold <= 0.0 or confidence_threshold >= 1.0:
        _error("quick fix: --confidence-threshold must be between 0 and 1 (exclusive)")
        sys.exit(1)

    autonomous_min_conf = args.autonomous_min_confidence
    if autonomous_min_conf <= 0.0 or autonomous_min_conf >= 1.0:
        _error("quick fix: --autonomous-min-confidence must be between 0 and 1 (exclusive)")
        sys.exit(1)

    autonomous_max_files = args.autonomous_max_files
    if autonomous_max_files <= 0:
        _error("quick fix: --autonomous-max-files must be 1 or higher")
        sys.exit(1)

    return {
        "project_filters": project_filters,
        "output_dir": output_dir,
        "inplace": inplace,
        "only_changed_or_added": only_changed_or_added,
        "time_window_start": time_window_start,
        "time_window_end": time_window_end,
        "hours_back": hours_back,
        "label_interactive": args.label_interactive,
        "interactive_top_k": top_k,
        "interactive_uncertain_only": args.interactive_uncertain_only,
        "confidence_threshold": confidence_threshold,
        "autonomous": args.autonomous,
        "autonomous_min_confidence": autonomous_min_conf,
        "autonomous_max_files": autonomous_max_files,
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


def _load_bytes(path, max_bytes=4096):
    try:
        with open(path, "rb") as f:
            return f.read(max_bytes)
    except OSError:
        return b""


def _safe_similarity(a_bytes, b_bytes):
    # tiny byte-level similarity: cheap, fast, and good enough for ranking hints.
    if not a_bytes and not b_bytes:
        return 1.0
    if not a_bytes or not b_bytes:
        return 0.0

    shortest = min(len(a_bytes), len(b_bytes))
    if shortest == 0:
        return 0.0
    same = sum(1 for i in range(shortest) if a_bytes[i] == b_bytes[i])
    return same / shortest


def _sigmoid(x):
    x = max(-60.0, min(60.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _clamp(value, low, high):
    return max(low, min(high, value))


def _confidence_from_margin(score_margin, temperature):
    # bigger top-vs-second margin -> higher confidence.
    t = max(0.2, float(temperature))
    return _sigmoid(score_margin / t)


def _init_bandit_state():
    arms = {}
    for arm in BANDIT_ARMS:
        arms[arm] = {"count": 0, "reward_sum": 0.0}
    return {
        "global": {
            "arms": arms,
            "total_pulls": 0,
        },
        "contexts": {},
        "epsilon": 0.12,
    }


def _empty_arm_stats():
    return {"count": 0, "reward_sum": 0.0}


def _new_bandit_bucket():
    return {
        "arms": {arm: _empty_arm_stats() for arm in BANDIT_ARMS},
        "total_pulls": 0,
    }


def _ensure_bandit_bucket(bucket):
    if not isinstance(bucket, dict):
        return _new_bandit_bucket()

    bucket.setdefault("arms", {})
    bucket.setdefault("total_pulls", 0)

    for arm in BANDIT_ARMS:
        arm_stats = bucket["arms"].get(arm)
        if not isinstance(arm_stats, dict):
            arm_stats = _empty_arm_stats()
            bucket["arms"][arm] = arm_stats
        arm_stats["count"] = int(arm_stats.get("count", 0))
        arm_stats["reward_sum"] = float(arm_stats.get("reward_sum", 0.0))

    bucket["total_pulls"] = int(bucket.get("total_pulls", 0))
    return bucket


def _bandit_context_key(relative_to_project, current_project_file, entries_in_window):
    # lightweight context features for arm selection only.
    suffix = relative_to_project.suffix.lower() if relative_to_project.suffix else "<none>"
    root = relative_to_project.parts[0].lower() if relative_to_project.parts else "<root>"
    depth = len(relative_to_project.parts)
    if depth <= 1:
        depth_bucket = "d1"
    elif depth <= 3:
        depth_bucket = "d2_3"
    else:
        depth_bucket = "d4p"

    history_count = len(entries_in_window)
    if history_count <= 1:
        history_bucket = "h1"
    elif history_count <= 4:
        history_bucket = "h2_4"
    else:
        history_bucket = "h5p"

    presence = "missing" if not current_project_file.exists() else "existing"
    return f"ext={suffix}|root={root}|depth={depth_bucket}|hist={history_bucket}|presence={presence}"


def _bandit_ucb_score(global_stats, context_stats, arm, total_context_pulls):
    ctx_count = float(context_stats[arm]["count"])
    ctx_reward = float(context_stats[arm]["reward_sum"])
    global_count = float(global_stats[arm]["count"])
    global_reward = float(global_stats[arm]["reward_sum"])

    global_mean = global_reward / max(1.0, global_count)
    prior_weight = 2.0
    blended_mean = (ctx_reward + prior_weight * global_mean) / max(1.0, ctx_count + prior_weight)

    # confidence bonus is tied to how much this context has been explored.
    bonus = math.sqrt(2.0 * math.log(max(2.0, float(total_context_pulls))) / max(1.0, ctx_count + 1.0))
    return blended_mean + bonus


def _init_model_state():
    return {
        "version": 1,
        "weights": dict(DEFAULT_WEIGHTS),
        "avg_weights": dict(DEFAULT_WEIGHTS),
        "learning_rate": LEARNING_RATE,
        "margin": MARGIN,
        "l2_reg": L2_REG,
        "lr_decay": LR_DECAY,
        "grad_clip": GRAD_CLIP,
        "temperature": DEFAULT_TEMPERATURE,
        "step": 0,
        "feedback_events": 0,
        "trained_pairs": 0,
        "recent_feedback": [],
        "drift_window": DRIFT_WINDOW,
        "bandit": _init_bandit_state(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _load_model_state():
    if not MODEL_STATE_FILE.exists():
        return _init_model_state()

    try:
        with open(MODEL_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        _warn("warning: model file was unreadable, creating a fresh one.")
        return _init_model_state()

    state.setdefault("weights", {})
    for k, v in DEFAULT_WEIGHTS.items():
        state["weights"].setdefault(k, v)

    state.setdefault("avg_weights", {})
    for k, v in DEFAULT_WEIGHTS.items():
        state["avg_weights"].setdefault(k, v)

    state.setdefault("learning_rate", LEARNING_RATE)
    state.setdefault("margin", MARGIN)
    state.setdefault("l2_reg", L2_REG)
    state.setdefault("lr_decay", LR_DECAY)
    state.setdefault("grad_clip", GRAD_CLIP)
    state.setdefault("temperature", DEFAULT_TEMPERATURE)
    state.setdefault("step", 0)
    state.setdefault("feedback_events", 0)
    state.setdefault("trained_pairs", 0)

    state.setdefault("recent_feedback", [])
    state.setdefault("drift_window", DRIFT_WINDOW)

    state.setdefault("bandit", _init_bandit_state())
    state["bandit"]["epsilon"] = float(state["bandit"].get("epsilon", 0.12))
    state["bandit"]["global"] = _ensure_bandit_bucket(state["bandit"].get("global"))
    state["bandit"].setdefault("contexts", {})

    contexts = state["bandit"]["contexts"]
    if not isinstance(contexts, dict):
        contexts = {}
        state["bandit"]["contexts"] = contexts
    for key in list(contexts.keys()):
        contexts[key] = _ensure_bandit_bucket(contexts.get(key))

    state.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
    return state


def _save_model_state(state):
    MODEL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    with open(MODEL_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _persist_recovery_state(manifest, model):
    MODEL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    _save_model_state(model)


def _print_recovery_summary(
    runtime,
    total_count,
    total_skipped_unchanged,
    total_skipped_no_candidates,
    total_interactive_labeled,
    total_autonomous_accepted,
    total_safety_prompts,
    early_exit=False,
):
    labels_example_path = MODEL_STATE_DIR / "labels.json"

    if early_exit:
        _warn("\nrun stopped early, but progress was saved safely.")

    _success(
        f"\nall done. recovered {total_count} files"
        f" (skipped {total_skipped_unchanged} unchanged, {total_skipped_no_candidates} no-candidate)."
    )
    _info("recovery session closed. your files should be waiting in the output folder.")
    if runtime["label_interactive"] or runtime["autonomous"]:
        _info(f"interactive labels collected this run: {total_interactive_labeled}")
    if runtime["autonomous"]:
        _info(f"autonomous accepts: {total_autonomous_accepted}")
        _info(f"autonomous safety prompts: {total_safety_prompts}")
    _info(f"manifest saved to: {MANIFEST_FILE}")
    _info(f"model saved to: {MODEL_STATE_FILE}")
    _info("\nnext step if you want to train the model:")
    print()
    _info(f"1) open this manifest and copy the correct candidate_id for each file: {MANIFEST_FILE}")
    print()
    _info("2) create a labels JSON file in this format:")
    _accent('   { "src/file.py": "candidate_id_here" }')
    print()
    _info(f"3) save it as: {labels_example_path}")
    print()
    _info("4) run training with:")
    _accent(
        f'   python {Path(__file__).name} --train --manifest "{MANIFEST_FILE}" --labels "{labels_example_path}"'
    )


def _dot(weights, features):
    return sum(weights.get(name, 0.0) * value for name, value in features.items())


def _inference_weights(model):
    # averaged weights usually feel less jumpy after many online updates.
    if int(model.get("step", 0)) > 0 and model.get("avg_weights"):
        return model["avg_weights"]
    return model["weights"]


def _build_features(candidate_path, current_project_file, rank_latest, now_ts):
    # lightweight features only: keeps everything offline and quick.
    source_stat = candidate_path.stat()
    source_size = float(source_stat.st_size)
    source_age_hours = max(0.0, (now_ts - source_stat.st_mtime) / 3600.0)

    missing = 0.0
    similarity = 0.0
    size_delta_ratio = 1.0

    if not current_project_file.exists():
        missing = 1.0
    else:
        current_size = float(current_project_file.stat().st_size)
        denom = max(1.0, current_size)
        size_delta_ratio = min(5.0, abs(source_size - current_size) / denom)

        source_bytes = _load_bytes(candidate_path)
        current_bytes = _load_bytes(current_project_file)
        similarity = _safe_similarity(source_bytes, current_bytes)

    return {
        "bias": 1.0,
        "rank_latest": rank_latest,
        "recency_hours": source_age_hours,
        "similarity_to_current": similarity,
        "size_delta_ratio": size_delta_ratio,
        "is_missing_in_current": missing,
    }


def _strategy_bonus(strategy_arm, features):
    # each arm biases ranking a little differently.
    rank_latest = features.get("rank_latest", 0.0)
    recency_hours = features.get("recency_hours", 0.0)
    similarity = features.get("similarity_to_current", 0.0)
    size_delta = features.get("size_delta_ratio", 0.0)
    is_missing = features.get("is_missing_in_current", 0.0)

    if strategy_arm == "recency_boost":
        return 0.35 * rank_latest - 0.008 * recency_hours
    if strategy_arm == "similarity_boost":
        return 0.35 * similarity - 0.15 * size_delta
    if strategy_arm == "missing_boost":
        return 0.55 * is_missing + 0.10 * rank_latest - 0.10 * similarity
    return 0.0


def _bandit_choose_arm(model, context_key):
    bandit = model["bandit"]
    global_bucket = _ensure_bandit_bucket(bandit.get("global"))
    bandit["global"] = global_bucket
    contexts = bandit.setdefault("contexts", {})
    context_bucket = _ensure_bandit_bucket(contexts.get(context_key))
    contexts[context_key] = context_bucket

    global_arms = global_bucket["arms"]
    context_arms = context_bucket["arms"]

    # decay exploration slowly over time.
    epsilon_base = float(bandit.get("epsilon", 0.12))
    step = max(0, int(model.get("step", 0)))
    epsilon = max(0.04, epsilon_base / (1.0 + step / 500.0))

    if random.random() < epsilon:
        return random.choice(BANDIT_ARMS)

    total_pulls = max(1, int(context_bucket.get("total_pulls", 0)))
    # cold-start in each context: force each arm to be tried at least once.
    for arm in BANDIT_ARMS:
        if int(context_arms[arm]["count"]) == 0:
            return arm

    best_arm = BANDIT_ARMS[0]
    best_score = -10**9
    for arm in BANDIT_ARMS:
        score = _bandit_ucb_score(global_arms, context_arms, arm, total_pulls)
        if score > best_score:
            best_score = score
            best_arm = arm

    return best_arm


def _bandit_update(model, context_key, arm, reward):
    if arm not in BANDIT_ARMS:
        return

    bandit = model["bandit"]
    global_bucket = _ensure_bandit_bucket(bandit.get("global"))
    bandit["global"] = global_bucket
    contexts = bandit.setdefault("contexts", {})
    context_bucket = _ensure_bandit_bucket(contexts.get(context_key))
    contexts[context_key] = context_bucket

    reward = float(reward)

    for bucket in (global_bucket, context_bucket):
        arm_state = bucket["arms"][arm]
        arm_state["count"] = int(arm_state.get("count", 0)) + 1
        arm_state["reward_sum"] = float(arm_state.get("reward_sum", 0.0)) + reward
        bucket["total_pulls"] = int(bucket.get("total_pulls", 0)) + 1


def _drift_signal(model):
    # 0.0 means stable, higher means "model is likely drifting".
    feedback = model.get("recent_feedback", [])
    if len(feedback) < DRIFT_MIN_SAMPLES:
        return 0.0

    window = feedback[-int(model.get("drift_window", DRIFT_WINDOW)):]
    short_n = min(10, len(window))
    short = window[-short_n:]

    long_acc = sum(window) / len(window)
    short_acc = sum(short) / len(short)

    # if short-term accuracy drops below long-term, that's a drift smell.
    drop = max(0.0, long_acc - short_acc)
    recent_error = 1.0 - short_acc

    signal = drop * 1.2 + max(0.0, recent_error - 0.25)
    return _clamp(signal, 0.0, 1.0)


def _adaptive_threshold(model, base_threshold):
    drift = _drift_signal(model)
    # drift up => ask more often.
    adjusted = float(base_threshold) + 0.20 * drift
    return _clamp(adjusted, 0.55, 0.95)


def _record_feedback(model, was_model_correct):
    feedback = model.setdefault("recent_feedback", [])
    feedback.append(1 if was_model_correct else 0)

    max_window = int(model.get("drift_window", DRIFT_WINDOW))
    if len(feedback) > max_window:
        del feedback[:len(feedback) - max_window]


def _online_pair_update(model, positive_features, negative_features):
    # pairwise ranking update: push chosen candidate above the rejected one.
    weights = model["weights"]
    avg_weights = model["avg_weights"]
    margin = float(model["margin"])
    base_lr = float(model["learning_rate"])
    lr_decay = float(model["lr_decay"])
    l2_reg = float(model["l2_reg"])
    grad_clip = float(model["grad_clip"])

    model["step"] = int(model.get("step", 0)) + 1
    step = model["step"]
    eta = base_lr / (1.0 + lr_decay * step)

    feature_names = set(positive_features.keys()) | set(negative_features.keys())
    diff = {
        name: positive_features.get(name, 0.0) - negative_features.get(name, 0.0)
        for name in feature_names
    }

    score_diff = sum(weights.get(name, 0.0) * value for name, value in diff.items())
    # logistic factor softens updates when ranking is already decent.
    logistic_coeff = _sigmoid(margin - score_diff)

    for name, value in diff.items():
        old_w = weights.get(name, 0.0)
        reg_term = l2_reg * old_w
        raw_update = eta * (logistic_coeff * value - reg_term)
        # clip updates so one weird example doesn't yank the model too hard.
        clipped_update = max(-grad_clip * eta, min(grad_clip * eta, raw_update))
        new_w = old_w + clipped_update
        weights[name] = new_w

        prev_avg = avg_weights.get(name, 0.0)
        avg_weights[name] = prev_avg + (new_w - prev_avg) / step

    model["trained_pairs"] = int(model.get("trained_pairs", 0)) + 1


def _online_train_choice(model, ranked_candidates, selected_candidate, predicted_confidence, was_model_correct):
    # treat user choice as ground truth and compare it against all other candidates.
    selected_features = selected_candidate.get("features", {})

    for candidate in ranked_candidates:
        if candidate["candidate_id"] == selected_candidate["candidate_id"]:
            continue
        _online_pair_update(model, selected_features, candidate.get("features", {}))

    temp = float(model.get("temperature", DEFAULT_TEMPERATURE))
    target = 1.0 if was_model_correct else 0.0
    # tiny temperature tune keeps confidence from getting too cocky.
    calibration_error = abs(target - predicted_confidence)
    if calibration_error > 0.30:
        temp = min(3.0, temp * 1.03)
    else:
        temp = max(0.4, temp * 0.997)
    model["temperature"] = temp
    model["feedback_events"] = int(model.get("feedback_events", 0)) + 1


def _load_labels(labels_path):
    try:
        with open(labels_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        _error(f"could not read labels file: {labels_path}")
        _warn(f"details: {exc}")
        sys.exit(1)

    if not isinstance(data, dict):
        _error("labels JSON must be an object: { \"relative/path\": \"candidate_id\" }")
        sys.exit(1)
    return data


def _fmt_ts_ms(ts_ms):
    if ts_ms is None:
        return "n/a"
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _confidence_vibe(confidence):
    if confidence >= 0.96:
        return "locked in", "model is very sure, basically caffeinated certainty"
    if confidence >= 0.85:
        return "pretty solid", "good odds this pick is right"
    if confidence >= 0.70:
        return "kinda sure", "worth a quick human eyeball"
    if confidence >= 0.55:
        return "squint mode", "model is guessing with mixed feelings"
    return "coin-flip vibes", "definitely review this one manually"


def _interactive_review(relative_to_project, ranked_candidates, confidence, top_k):
    # quick terminal review: accept top pick or override it.
    display_count = min(top_k, len(ranked_candidates))
    vibe_label, vibe_note = _confidence_vibe(confidence)

    _accent(f"\nreview: {relative_to_project}")
    _info(f"model confidence: {confidence:.3f}")
    _info(f"confidence vibe: {vibe_label} ({vibe_note})")
    _accent("top candidates:")

    for idx in range(display_count):
        c = ranked_candidates[idx]
        _accent(
            f"  [{idx + 1}] id={c['candidate_id']}  score={c['score']:.4f}  ts={_fmt_ts_ms(c.get('timestamp'))}"
        )

    while True:
        answer = input("accept top pick? ([Enter]/y=yes, n=choose, s=skip file, q=quit run): ").strip().lower()
        if answer in ("y", "yes", ""):
            _success("nice, shipping the top pick.")
            return ranked_candidates[0], "accept"
        if answer in ("s", "skip"):
            _warn("skip noted. this file can chill for now.")
            return None, "skip"
        if answer in ("q", "quit"):
            _warn("quit requested. saving progress before exit.")
            return None, "quit"
        if answer in ("n", "no"):
            choice = input(f"pick candidate number (1-{display_count}): ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < display_count:
                    _info(f"manual override locked: candidate #{idx + 1}.")
                    return ranked_candidates[idx], "choose"
            _warn("that choice wasn't valid, try again.")
            continue
        _warn("please answer y / n / s / q.")


def train_from_manifest(manifest_path, labels_path):
    # batch mode for when you label from manifest after the run.
    model = _load_model_state()
    labels = _load_labels(labels_path)

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        _error(f"could not read manifest: {manifest_path}")
        _warn(f"details: {exc}")
        sys.exit(1)

    recovered = manifest.get("files", [])
    if not recovered:
        _warn("manifest has no files. nothing to train.")
        return

    updates_before = int(model.get("trained_pairs", 0))
    seen = 0

    for item in recovered:
        rel = item.get("relative_path")
        candidates = item.get("candidates", [])
        if not rel or not candidates:
            continue

        label_candidate_id = labels.get(rel)
        if not label_candidate_id:
            continue

        by_id = {c.get("candidate_id"): c for c in candidates}
        positive = by_id.get(label_candidate_id)
        if not positive:
            continue

        seen += 1
        for negative in candidates:
            neg_id = negative.get("candidate_id")
            if neg_id == label_candidate_id:
                continue
            _online_pair_update(model, positive.get("features", {}), negative.get("features", {}))

    updates = int(model.get("trained_pairs", 0)) - updates_before
    _save_model_state(model)

    _success("\ntraining complete. model did some brain reps.")
    _info(f"labeled files used: {seen}")
    _info(f"pairwise updates: {updates}")
    _info(f"model saved to: {MODEL_STATE_FILE}")
    _info("\nlabel format reminder:")
    _accent('{ "src/file.py": "<candidate_id_from_manifest>" }')


def recover(runtime):
    # main flow: scan history, rank candidates, optionally ask human, then restore.
    history_path, history_candidates = _find_history_path()
    if not history_path:
        _error("man, i can't find a VS Code history folder 😩")
        _warn("i checked these paths:")
        for candidate in history_candidates:
            _accent(f"  - {candidate}")
        return

    start_dt = _parse_time_window(runtime["time_window_start"], "TIME_WINDOW_START")
    end_dt = _parse_time_window(runtime["time_window_end"], "TIME_WINDOW_END")

    hours_back = runtime["hours_back"]
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

    model = _load_model_state()
    now_ts = datetime.now().timestamp()

    output_dir = runtime["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model_file": str(MODEL_STATE_FILE),
        "files": [],
    }

    total_count = 0
    total_skipped_unchanged = 0
    total_skipped_no_candidates = 0
    total_interactive_labeled = 0
    total_autonomous_accepted = 0
    total_safety_prompts = 0

    for project_filter in runtime["project_filters"]:
        filter_path = Path(project_filter).resolve()
        project_name = filter_path.name

        if runtime["inplace"]:
            project_output_dir = filter_path / "vscode_history_recovered_ml"
        else:
            project_output_dir = output_dir / project_name

        _info(f"\nstarting project: {project_name}")
        _info("note: recoveries are copy-only, your current files stay untouched.")

        if project_output_dir.exists():
            _warn(f"\n🔥 whoa, slow down! '{project_output_dir}' already exists.")
            _warn("   if we keep going we'll stomp whatever's in there.")
            print()  # just a little spacing for readability
            _warn("   type 'yes' to overwrite, 'no' to skip this project, or 'nuke' to delete the whole folder and start fresh.")
            print()  # more spacing for readability
            answer = input("   what's your pick? (yes / no / nuke): ").strip().lower()
            if answer == "nuke":
                shutil.rmtree(project_output_dir)
                project_output_dir.mkdir(parents=True)
            elif answer != "yes":
                _warn(f"   OK, skipping {project_name} this round.")
                continue

        project_output_dir.mkdir(parents=True, exist_ok=True)

        recovered_for_project = 0
        skipped_unchanged = 0
        skipped_no_candidates = 0

        for folder in history_path.iterdir():
            if not folder.is_dir():
                continue

            entries_file = folder / "entries.json"
            if not entries_file.exists():
                continue

            try:
                with open(entries_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue

            original_uri = data.get("resource")
            entries = data.get("entries", [])
            if not original_uri or not entries:
                continue

            original_file_path = _resource_uri_to_path(original_uri)
            if original_file_path is None:
                continue

            try:
                relative_to_project = original_file_path.relative_to(filter_path)
            except ValueError:
                continue

            entries.sort(key=lambda x: x.get("timestamp", 0))

            entries_in_window = []
            for entry in entries:
                ts = entry.get("timestamp")
                if ts is None:
                    continue

                entry_dt = datetime.fromtimestamp(ts / 1000)
                if start_dt and entry_dt < start_dt:
                    continue
                if end_dt and entry_dt > end_dt:
                    continue
                entries_in_window.append(entry)

            if not entries_in_window:
                skipped_no_candidates += 1
                continue

            current_project_file = filter_path / relative_to_project
            context_key = _bandit_context_key(relative_to_project, current_project_file, entries_in_window)
            strategy_arm = _bandit_choose_arm(model, context_key)
            candidates = []
            total_window = len(entries_in_window)

            for idx, entry in enumerate(entries_in_window):
                candidate_id = entry.get("id")
                if not candidate_id:
                    continue
                candidate_path = folder / candidate_id
                if not candidate_path.exists():
                    continue

                if total_window == 1:
                    rank_latest = 1.0
                else:
                    rank_latest = idx / (total_window - 1)

                try:
                    features = _build_features(candidate_path, current_project_file, rank_latest, now_ts)
                except OSError:
                    continue

                base_score = _dot(_inference_weights(model), features)
                score = base_score + _strategy_bonus(strategy_arm, features)
                candidates.append(
                    {
                        "candidate_id": candidate_id,
                        "timestamp": entry.get("timestamp"),
                        "base_score": base_score,
                        "score": score,
                        "features": features,
                        "path": str(candidate_path),
                    }
                )

            if not candidates:
                skipped_no_candidates += 1
                continue

            ranked = sorted(candidates, key=lambda c: c["score"], reverse=True)
            predicted = ranked[0]

            if len(ranked) > 1:
                score_margin = ranked[0]["score"] - ranked[1]["score"]
            else:
                score_margin = 5.0

            confidence = _confidence_from_margin(score_margin, model.get("temperature", DEFAULT_TEMPERATURE))
            drift_signal = _drift_signal(model)
            adaptive_threshold = _adaptive_threshold(model, runtime["confidence_threshold"])

            selected = predicted
            interactive_action = "auto"
            did_label = False

            if runtime["autonomous"]:
                # safety gates: confidence, drift health, risk profile, and auto budget.
                risky_size_jump = (
                    predicted["features"].get("size_delta_ratio", 0.0) > 2.5
                    and predicted["features"].get("similarity_to_current", 0.0) < 0.2
                )
                confidence_gate = confidence >= max(runtime["autonomous_min_confidence"], adaptive_threshold + 0.05)
                drift_gate = drift_signal < DRIFT_ALERT_LEVEL
                budget_gate = total_autonomous_accepted < runtime["autonomous_max_files"]

                if confidence_gate and drift_gate and budget_gate and not risky_size_jump:
                    interactive_action = "autonomous-accept"
                    total_autonomous_accepted += 1
                    _success(
                        f"autonomous greenlight: conf={confidence:.3f}, drift={drift_signal:.3f}, arm={strategy_arm}"
                    )
                else:
                    total_safety_prompts += 1
                    _warn(
                        "autonomous pause: safety gate asked for human review "
                        f"(conf={confidence:.3f}, drift={drift_signal:.3f}, arm={strategy_arm})"
                    )
                    selected, interactive_action = _interactive_review(
                        relative_to_project,
                        ranked,
                        confidence,
                        runtime["interactive_top_k"],
                    )

                    if interactive_action == "quit":
                        _warn("\nautonomous safety quit requested. saving progress and wrapping up.")
                        total_count += recovered_for_project
                        total_skipped_unchanged += skipped_unchanged
                        total_skipped_no_candidates += skipped_no_candidates
                        _persist_recovery_state(manifest, model)
                        _print_recovery_summary(
                            runtime,
                            total_count,
                            total_skipped_unchanged,
                            total_skipped_no_candidates,
                            total_interactive_labeled,
                            total_autonomous_accepted,
                            total_safety_prompts,
                            early_exit=True,
                        )
                        return

                    if interactive_action == "skip":
                        continue

                    did_label = True
                    total_interactive_labeled += 1

            elif runtime["label_interactive"]:
                should_query = True
                if runtime["interactive_uncertain_only"]:
                    # active learning mode: only bug you when model is unsure.
                    should_query = confidence < adaptive_threshold

                if should_query:
                    selected, interactive_action = _interactive_review(
                        relative_to_project,
                        ranked,
                        confidence,
                        runtime["interactive_top_k"],
                    )

                    if interactive_action == "quit":
                        _warn("\ninteractive quit requested. saving progress and wrapping up.")
                        total_count += recovered_for_project
                        total_skipped_unchanged += skipped_unchanged
                        total_skipped_no_candidates += skipped_no_candidates
                        _persist_recovery_state(manifest, model)
                        _print_recovery_summary(
                            runtime,
                            total_count,
                            total_skipped_unchanged,
                            total_skipped_no_candidates,
                            total_interactive_labeled,
                            total_autonomous_accepted,
                            total_safety_prompts,
                            early_exit=True,
                        )
                        return

                    if interactive_action == "skip":
                        continue

                    did_label = True
                    total_interactive_labeled += 1
                elif runtime["interactive_uncertain_only"]:
                    _info(
                        f"auto-pass on prompt: confidence {confidence:.3f} is above adaptive threshold {adaptive_threshold:.3f}"
                    )

            source_file = Path(selected["path"])

            if runtime["only_changed_or_added"] and current_project_file.exists():
                try:
                    if filecmp.cmp(source_file, current_project_file, shallow=False):
                        _warn(f"unchanged, skipped: {relative_to_project}")
                        skipped_unchanged += 1
                        continue
                except OSError:
                    pass

            target_file = project_output_dir / relative_to_project
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
            _success(
                f"restored (ml-picked): {relative_to_project} "
                f"[conf={confidence:.3f}, arm={strategy_arm}, action={interactive_action}]"
            )
            recovered_for_project += 1

            if did_label:
                # instant learning: feedback updates model in the same run.
                was_model_correct = selected["candidate_id"] == predicted["candidate_id"]
                _online_train_choice(model, ranked, selected, confidence, was_model_correct)
                _record_feedback(model, was_model_correct)
                _bandit_update(model, context_key, strategy_arm, 1.0 if was_model_correct else 0.0)

            manifest["files"].append(
                {
                    "project": str(filter_path),
                    "relative_path": str(relative_to_project).replace("\\", "/"),
                    "predicted_candidate_id": predicted["candidate_id"],
                    "predicted_score": predicted["score"],
                    "selected_candidate_id": selected["candidate_id"],
                    "selected_score": selected["score"],
                    "confidence": confidence,
                    "drift_signal": drift_signal,
                    "adaptive_threshold": adaptive_threshold,
                    "strategy_arm": strategy_arm,
                    "bandit_context": context_key,
                    "interactive_action": interactive_action,
                    "was_model_correct": selected["candidate_id"] == predicted["candidate_id"],
                    "candidates": [
                        {
                            "candidate_id": c["candidate_id"],
                            "timestamp": c["timestamp"],
                            "base_score": c["base_score"],
                            "score": c["score"],
                            "features": c["features"],
                        }
                        for c in ranked
                    ],
                }
            )

        _success(
            f"\nall done for project '{project_name}'. recovered {recovered_for_project} files"
            f" (skipped {skipped_unchanged} unchanged, {skipped_no_candidates} no-candidate)"
            f" to: {project_output_dir}"
        )
        _info("project wrap-up complete. onward.")

        total_count += recovered_for_project
        total_skipped_unchanged += skipped_unchanged
        total_skipped_no_candidates += skipped_no_candidates

    _persist_recovery_state(manifest, model)
    _print_recovery_summary(
        runtime,
        total_count,
        total_skipped_unchanged,
        total_skipped_no_candidates,
        total_interactive_labeled,
        total_autonomous_accepted,
        total_safety_prompts,
        early_exit=False,
    )


def main():
    # route into either training mode or recovery mode.
    parser = _build_arg_parser()
    args = parser.parse_args()

    _info("\n" + "=" * 60)
    _info("   ✨ VSCode Local History Recovery Tool (Offline ML) ✨")
    _info("=" * 60 + "\n")
    _info("Hey there, I’m about to raid VS Code's local history vault and recover snapshots for you. 🛟")
    _info("I’ll rank candidates, ask when confidence gets sketchy, and keep your current files untouched. ☕\n")

    if args.autonomous:
        _accent("autonomous mode is ON (safety gates enabled).")
    elif args.label_interactive:
        _accent("interactive label mode is ON (human-in-the-loop learning).")

    if args.train:
        manifest_path = Path(args.manifest).expanduser() if args.manifest else MANIFEST_FILE
        if not args.labels:
            _error("training mode needs --labels labels.json")
            sys.exit(1)
        labels_path = Path(args.labels).expanduser()
        train_from_manifest(manifest_path, labels_path)
        return

    runtime = _resolve_runtime_settings(args)
    recover(runtime)


if __name__ == "__main__":
    main()
