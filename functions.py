"""Built-in actions TriggerPanel runs itself — no external program needed.

Standalone (this is exactly what the app does):

    python functions.py                list every monitor + its brightness
    python functions.py -f 50         fade every monitor to 50%
    python functions.py -s 50         set 50% immediately, no fade
    python functions.py 50            same as -f 50 (the shortcut)
    python functions.py -f 50 -d 0    only display 0 (repeat -d, or use a name)
    python functions.py -f 50 --speed fast   slow | normal | fast
    python functions.py -g            read only

Every selected display starts its fade on its own thread and they are joined
at the end, so the panels ramp together rather than one after another.

The flags mirror the library's own CLI (`python -m screen_brightness_control
-d 0 -f 50`) and the fade is the library's own curve — 10 ms steps along a
logarithmic ramp; `--speed` sets how many percent each step moves.

Nothing here is hard-coded: displays are discovered when the command runs, and
the engine (screen-brightness-control) picks whatever the machine actually
supports — DDC/CI over the display cable for desktop panels, the ACPI panel
API for laptop lids. The same file works on any Windows machine with any
monitor.
"""

import argparse
import re
import sys

try:
    import screen_brightness_control as _sbc
except ImportError:  # pragma: no cover - only hit when the dep is missing
    sys.stderr.write(
        "screen-brightness-control is not installed in this interpreter:\n"
        "    python -m pip install screen-brightness-control\n"
    )
    _sbc = None


# ── display discovery ────────────────────────────────────────────────────────

def monitors():
    """Every display this machine can reach.

    Returns [{'index', 'name', 'id', 'model', 'serial'}, …] — names and ids
    come from the panel itself (EDID), so they are whatever the monitor
    reports; an empty name is labelled 'Display N' by the caller.
    """
    found = []
    for info in _sbc.list_monitors_info():
        name = (info.get("name") or "").strip()
        name = re.sub(r"(?i)^none\s+", "", name).strip()
        found.append({
            "index": len(found),
            "name": name,
            "id": str(info.get("uid") or info.get("manufacturer_id") or ""),
            "model": (info.get("model") or "").strip(),
            "serial": (info.get("serial") or "").strip(),
        })
    return found


def label(m: dict) -> str:
    """One line describing a display: 'Display 1 — LG … · id 4356'."""
    name = m["name"] or m["model"] or "Display %d" % (m["index"] + 1)
    return "Display %d — %s%s" % (
        m["index"] + 1, name,
        " · id %s" % m["id"] if m["id"] else "")


def read(index: int):
    """Current brightness of one display, or None if it won't answer."""
    try:
        return _sbc.get_brightness(display=index)[0]
    except Exception:
        return None


# Fade speed = how many percent each step moves, every 10 ms (the library's
# own interval). Measured on Tripy's two panels, both ramping at once, 100→20:
#   slow 1% ≈ 5.1 s   normal 2% ≈ 3.6 s   fast 5% ≈ 2.0 s
FADE_SPEEDS = {"slow": 1, "normal": 2, "fast": 5}
FADE_SPEED_ORDER = ("slow", "normal", "fast")   # how the app lists them
DEFAULT_SPEED = "normal"


def start_fade(index: int, value: int, speed: str = DEFAULT_SPEED):
    """Begin a fade on one display WITHOUT waiting for it.

    Returns the library's threads for it, so several displays can ramp at the
    same time and the caller joins them at the end.
    """
    return _sbc.fade_brightness(value, display=index,
                               increment=FADE_SPEEDS.get(speed, 2),
                               blocking=False)


def set_one(index: int, value: int, fade: bool = True,
            speed: str = DEFAULT_SPEED):
    """Move one display to a value and wait for it. (ok, error-or-empty)."""
    try:
        if fade:
            for t in start_fade(index, value, speed):
                t.join()
        else:
            _sbc.set_brightness(value, display=index)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def pick_displays(spec: str, found: list) -> list:
    """Resolve a user's monitor selection into display indexes.

    spec: "all", "" (same as all), a comma list of indexes ("0,2"), or a
    comma list of (partial) monitor names ("lg,pa329").
    """
    spec = (spec or "").strip().lower()
    if spec in ("", "all", "*"):
        return [m["index"] for m in found]
    wanted = [w.strip() for w in spec.split(",") if w.strip()]
    picked = []
    for w in wanted:
        if w.isdigit():
            i = int(w)
            if 0 <= i < len(found):
                picked.append(i)
            continue
        for m in found:  # name match, so a machine's own labels work too
            haystack = " ".join(m.get(k) or "" for k in ("name", "model", "id"))
            if w in haystack.lower():
                picked.append(m["index"])
    # de-duplicate, keep order
    seen, out = set(), []
    for i in picked:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# ── the registry the app offers ──────────────────────────────────────────────

def brightness_detail(entry: dict) -> str:
    """One line for the app's Details column — no side effects."""
    if entry.get("fade", True):
        how = "fade to %s%% (%s)" % (entry.get("value", "?"),
                                     entry.get("speed", DEFAULT_SPEED))
    else:
        how = "set %s%%" % entry.get("value", "?")
    return "%s on %s" % (how, entry.get("monitors", "all") or "all")


def run_brightness(entry: dict) -> str:
    """Move the chosen displays to a value, all of them at the same time.

    Every fade is started on its own thread and only then joined, so the
    panels ramp together instead of one after the other. The result reports
    what each panel actually reports afterwards, not what we asked for.
    """
    value = int(entry.get("value"))
    if not 0 <= value <= 100:
        raise ValueError("brightness must be between 0 and 100")
    fade = bool(entry.get("fade", True))
    speed = str(entry.get("speed", DEFAULT_SPEED))
    found = monitors()
    if not found:
        raise ValueError("no controllable displays found on this machine")
    indexes = pick_displays(str(entry.get("monitors", "all")), found)
    if not indexes:
        raise ValueError("selected monitors are not available")
    by_index = {m["index"]: m for m in found}

    def name_of(i):
        m = by_index[i]
        return m["name"] or m["model"] or "#%d" % i

    threads, started, failed = [], [], []
    for i in indexes:
        try:
            if fade:
                threads += start_fade(i, value, speed)
            else:
                _sbc.set_brightness(value, display=i)
            started.append(i)
        except Exception as exc:
            failed.append("%s: %s" % (name_of(i), exc))
    for t in threads:
        t.join()

    done = []
    for i in started:
        got = read(i)
        done.append("%s=%s%%" % (name_of(i), value if got is None else got))
    if not done:
        raise ValueError("; ".join(failed) or "no display accepted the value")
    return "%s %d%% on %d/%d displays (%s)%s" % (
        "faded to" if fade else "set", value, len(done), len(indexes),
        ", ".join(done),
        " — refused: " + "; ".join(failed) if failed else "")


FUNCTIONS = {
    "brightness": {
        "label": "Monitor brightness",
        "hint": ("Sets the panel's OWN brightness (the number its menu shows) "
                 "— not Windows' gamma. Monitors are found at run time; "
                 "value is 0–100; fade ramps to it instead of jumping."),
        "detail": brightness_detail,
        "run": run_brightness,
        "icon": "tabler:brightness",   # offered to the wizard as a starting point
    },
}


def run(name: str, entry: dict) -> str:
    """Entry point used by TriggerPanel's 'func' entry type."""
    fn = FUNCTIONS.get(name)
    if fn is None:
        raise ValueError("unknown function %r" % name)
    return fn["run"](entry)


# ── command line ─────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    """Command line, shaped like the library's own:
    python -m screen_brightness_control [-d DISPLAY] [-s VALUE] [-g] [-f VALUE]
    """
    ap = argparse.ArgumentParser(
        description="set or fade monitor brightness on this machine's displays")
    ap.add_argument("value", type=int, nargs="?", default=None,
                    help="shortcut for -f VALUE")
    ap.add_argument("-f", "--fade", type=int, default=None,
                    help="fade to this brightness (0-100)")
    ap.add_argument("-s", "--set", dest="set_to", type=int, default=None,
                    help="set this brightness immediately (0-100)")
    ap.add_argument("-d", "--display", action="append", default=[],
                    help="restrict to an index or name (repeatable); "
                         "default: every display")
    ap.add_argument("-g", "--get", action="store_true", help="read only")
    ap.add_argument("--speed", choices=list(FADE_SPEED_ORDER), default=DEFAULT_SPEED,
                    help="how fast a fade moves (default: %s)" % DEFAULT_SPEED)
    args = ap.parse_args(argv)

    if _sbc is None:
        return 1

    # one action wins: -f, then -s, then the bare number (a fade shortcut)
    if args.fade is not None:
        value, fade = args.fade, True
    elif args.set_to is not None:
        value, fade = args.set_to, False
    elif args.value is not None:
        value, fade = args.value, True
    else:
        value, fade = None, True

    if value is not None and not 0 <= value <= 100:
        sys.stderr.write("value must be between 0 and 100\n")
        return 1

    found = monitors()
    if not found:
        sys.stderr.write("no controllable displays found on this machine\n")
        return 1

    if value is None or args.get:
        for m in found:
            v = read(m["index"])
            print("%s | %s%%" % (label(m), v if v is not None else "?"))
        return 0

    # same code path the app uses, so the terminal cannot behave differently
    try:
        print(run_brightness({"value": value,
                             "monitors": ",".join(args.display),
                             "fade": fade, "speed": args.speed}))
    except ValueError as exc:
        sys.stderr.write("%s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
