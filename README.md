# TriggerPanel

Tap a shortcut on your iPhone → an app launches on this PC.

TriggerPanel is a **native PyQt6 desktop app** — dark theme styled like
TradingBot (Catppuccin Mocha, `#181825` base, `#89b4fa` accent) — with a
built-in HTTP server and a system-tray icon. Each app you register gets a
**permanent index number** (`#1`, `#2`, …) and your phone just calls:

```
http://<YOUR-IP>:8765/run?app=1&token=<token>
```

URLs use your PC's **LAN IP**. Reserve it in your router (DHCP reservation)
so it never changes.

## Entry types

Entries aren't just apps — when you add one, a **frameless wizard overlay**
(style matches TradingBot's popups: dark card, light border, drop shadow,
Escape / click-outside to dismiss) asks what it should do:

| Type | What it does |
| --- | --- |
| **Run an app** | Launch an `.exe`, `.lnk`, document, URI (`spotify:`) or command line |
| **Python script** | Run a `.py` file with its own virtualenv's interpreter — auto-detects `venv/` / `.venv/` next to the script, or set an explicit venv path |
| **Send keystrokes** | Press a combo — or a series (`ctrl+shift+s, enter`) — via `SendInput`. Keys go to the window focused on your PC when it fires. Can't inject into windows running as admin (UIPI) |
| **Call an API endpoint** | HTTP `GET/POST/PUT/PATCH/DELETE` with optional body. 4xx/5xx counts as "called"; only connection failures error |
| **Run Function** | A built-in action that runs inside TriggerPanel — no script, no venv. Today: **Monitor brightness** (sets the panel's own OSD brightness on the displays it finds) |

**Run Function is machine-agnostic**: the displays are discovered when the
wizard opens and when the entry fires — nothing per-machine is ever stored.
Its page shows **one checkbox per detected display**, labelled with what the
panel itself reports (name, model or `Display N` when the monitor gives no
name) plus the monitor's own id, e.g.

```
☑ Display 1 — LG Electronics ULTRAGEAR+ · id 4356
☑ Display 2 — PA329CV · id 4353
```

Uncheck the ones you don't want; leave them all checked for "every display".
A `Value` field takes 0–100, and a **Fade** checkbox decides whether the panels
*ramp* to it or jump straight there. Every selected display starts its fade on
its own thread and they are joined at the end — **the screens move together**,
not one after the other. **Fade speed** offers `slow / normal / fast`
(measured on these two panels, both at once, 100 → 20: 5.1 s / 3.6 s / 2.0 s).
**Test now** runs exactly those options right there — the screens change, the
result line reports what each panel actually ended up at, and **nothing is
saved** until you press Add entry. The same entry works on any Windows machine
with any DDC/CI panel — and on laptop lids, which use the ACPI path instead.

**Icon picker:** every entry's Icon row has a **Find icons** field — type a
word (`brightness`, `spotify`, `volume`…) and it offers up to five matching
ids from the same service the phone panel renders through; tap one and it
fills the Icon field. Built-ins also suggest a starting icon on their own.
Offline it says so plainly and leaves the field alone.

```
python functions.py                 list every monitor + its brightness
python functions.py -f 50          fade every monitor to 50%
python functions.py -s 50          set 50% immediately, no fade
python functions.py 50             same as -f 50
python functions.py -f 50 -d 0     only display 0 (repeat -d, or use a name)
python functions.py -f 50 --speed fast   slow | normal | fast
```
Flags mirror `python -m screen_brightness_control -d 0 -f 50`, and
`functions.py` is the exact code the app runs — the terminal and the shortcut
URL do the same thing.

The list shows each entry's **type** (color-coded: blue app / purple script / green keys /
yellow http / pink function) and details, and `/run` responses include
`{"ok": true, "type": ..., "detail": ...}` so your phone notification tells
you exactly what happened.

## The desktop app

| Control | What it does |
| --- | --- |
| **+ New entry** | Opens the 2-step wizard overlay (pick type → details, incl. **Icon** id) |
| **⚙ Settings** | Overlay with **Run at boot**, **port**, and **token** + **Generate** button — token applies immediately, port on restart |
| **Run** (or double-click) | Run the selected entry now |
| **Edit** | Reopen the wizard prefilled — change the name, icon, action, or even the type; the entry keeps its number |
| **Copy URL** | Copy that entry's ready-made shortcut URL |
| **Token** field | Click to select, **Copy** button — paste into your shortcut |
| **Delete** | Remove it — other entries keep their numbers |
| **Minimize / ✕** | Hides to the **system tray** (server keeps running) |
| Tray **Show Window** | Bring the window back |
| Tray **Exit** / **Quit** | Stop the server and exit (shortcuts stop working!) |
| Title bar | Drag to move, double-click to maximize, 8px edges resize |

## PC setup

1. **Install** — `install.bat` (venv + PyQt6).
2. **Firewall** — right-click `add-firewall.bat` → **Run as administrator**.
3. **Autostart** — already configured: the Startup shortcut launches
   `TriggerPanel.exe` at sign-in.
4. **Start now** — `start.bat`, or the already-running instance.
5. **Reserve your IP** in the router (DHCP reservation / static lease) so
   `http://<YOUR-IP>:8765` never breaks.
6. **Add entries** via **+ New entry** (wizard); use **Copy URL** on each row.

## Windows .exe (what Startup runs)

`TriggerPanel.exe` at the project root is a self-contained build (PyInstaller,
onefile, windowed, icon embedded) — **no Python required to run it** — and
it's what the Startup shortcut launches. It reads `config.json` /
`server.log` sitting next to it (same token and entries as source runs).

Rebuild after code changes:

```bat
venv\Scripts\python -m PyInstaller --noconfirm --onefile --windowed --name TriggerPanel --icon app_icon.ico --distpath . --workpath build server.py
```

Notes: the onefile bundle unpacks on every start (~2–4 s extra at login),
and because the exe is unsigned, Windows SmartScreen may show a one-time
“More info → Run anyway” prompt. Settings → **Run at boot** always points
the Startup shortcut at the exe when it exists.

## Phone panel, /list & icons

The built-in server hosts a phone-friendly **Apple-style button page**:

| Endpoint | Returns |
| --- | --- |
| `GET /list?token=…` | JSON: a ready-made `page` link plus every entry's `run` URL (id, name, type, icon) |
| `GET /panel?token=…` | The hosted page — iOS-style icon tiles in a grid; tap one to trigger it (✓/✗ badge + toast) |
| `GET /icon` | PNG used as the panel's Apple touch icon (for Add to Home Screen) |

The panel also has two guide chips: **⌂ Add to Home Screen** (Safari →
Share → Add to Home Screen) and **⚡ Make an iOS Shortcut** — copy an
entry's URL, then tap **Open Shortcut editor**, which deep-links via
`shortcuts://create-shortcut` straight into a new shortcut on your iPhone.

**Icons:** set an **Icon** id on an entry in the wizard, e.g.
`flowbite:bug-solid` — browse ids at
[iconvaultkit.com/icons](https://iconvaultkit.com/icons) (200k+ free icons) —
or paste a full image URL instead. The panel renders Iconify ids through
`api.iconify.design` (IconVaultKit has no icon-by-id endpoint of its own);
entries without an icon show their number, iOS-app-style.

Open `http://<YOUR-IP>:8765/panel?token=<token>` from your phone — the
status page at `/` links to both endpoints.

## iPhone setup (Shortcuts)

1. Phone and PC on the **same Wi-Fi**.
2. Shortcuts app → **+** → search **"Get Contents of URL"**.
3. Paste the URL from the app's **Copy URL** button
   (e.g. `http://192.168.0.116:8765/run?app=1&token=…`).
4. Optional: add **Show Notification** after it and pass
   *Contents of URL* — you'll see `{"ok": true, "app": 1, ...}` or the error.
5. Run it — the app opens on the PC.

One shortcut per app: duplicate and change `app=1` → `app=2`, etc.
Or one shortcut with **Choose from List** → a separate
*Get Contents of URL* action per choice.

Handy triggers: **Back Tap** (Settings → Accessibility → Touch → Back Tap)
or a Home Screen icon (Share → Add to Home Screen).

## Files

| File                    | Purpose                                   |
| ----------------------- | ----------------------------------------- |
| `server.py`             | GUI (PyQt6) + tray + HTTP server (one file) |
| `functions.py`          | Built-in actions (Run Function) + the same tool as a command line |
| `config.json`           | Port, token, your app list                |
| `start.bat`             | Launch now                                |
| `install.bat`           | Create/repair venv + install PyQt6        |
| `add-firewall.bat`      | Allow TCP 8765 inbound (run as admin)     |
| `install-autostart.ps1` | Start at Windows sign-in                  |
| `requirements.txt`      | `PyQt6`                                   |
| `server.log`            | Every launch, plus errors                 |

## Notes

- **Index numbers are permanent** — deleting `#2` never renumbers the others,
  so existing shortcut URLs keep working. New ids come from a persisted
  counter and are never reused.
- The `token` in `config.json` is a secret: anyone who has it can launch
  apps on your PC. Don't share the full URL publicly.
- Changed the port? Edit `config.json`, re-run `add-firewall.bat` with the
  new port, restart TriggerPanel.
- Troubleshooting: open `http://<YOUR-IP>:8765/health` on the phone, check
  `server.log`, confirm same Wi-Fi, confirm the firewall rule exists.
