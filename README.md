# Manipulator

A small desktop utility for bulk filename editing with regex filtering. Point it at a file or a directory, optionally narrow the set with a regex, and apply one of four operations. A preview shows the planned changes before anything touches disk.

## Features

- **Four rename operations** (one active at a time):
  - **Append** text before the file extension (`movie.srt` + `.en` -> `movie.en.srt`)
  - **Prepend** text to the start of the filename
  - **Remove** regex matches from the filename
  - **Replace** regex matches with a replacement string
- **Regex filter** to limit which files are touched. Supports invert (edit only files that do *not* match) and an optional case-insensitive mode.
- **Recursive** directory walk (off by default).
- **Single-file mode**: drop a path to one specific file instead of a directory.
- **Drag-and-drop** the path into the Path field from your file manager.
- **Preview** lists the proposed renames in a table; collisions with existing files are flagged in red and block Apply.
- **Light / dark theme** toggle (sv-ttk).
- **Optional persistence**: remember window position and/or last used inputs across launches. Config can live in the OS-standard per-user location or alongside the executable for a portable install.
- Handles UNC paths (`\\server\share\...`), mapped drives, and Unicode filenames.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for dependency management

Dependencies (managed by uv via `pyproject.toml` / `uv.lock`):

- `tkinterdnd2` - drag-and-drop support for Tk
- `sv-ttk` - light/dark ttk theme

The GUI itself uses stdlib Tkinter / ttk.

## Run from source

```
uv sync
uv run main.py
```

## Build a standalone executable

Local build (one platform at a time, since PyInstaller is not a cross-compiler):

```
uv run --with pyinstaller pyinstaller \
    --noconsole \
    --onedir \
    --noupx \
    --clean \
    --name Manipulator \
    --collect-all tkinterdnd2 \
    --collect-all sv_ttk \
    main.py
```

The output appears in `dist/Manipulator/`. macOS works from source but is not part of the release pipeline — build locally if you need a `.app`.

### CI release builds

`.github/workflows/build-release.yaml` builds Windows x64 and Linux x64 (Debian-compatible glibc) binaries on every `v*` tag push and publishes them as a GitHub Release. A `workflow_dispatch` trigger is included so the pipeline can be run manually without cutting a tag.

## Usage notes

- **Filter is `re.search` against the bare filename**, not the full path. Empty filter = all candidates.
- **The Replace and Remove fields take regex.** Append and Prepend are literal text.
- **Collision detection** uses `Path.samefile`, so a case-only rename on Windows (`MOVIE.srt` -> `movie.srt`) is not falsely flagged as a conflict.
- **Window position recovery**: if the saved geometry would land off-screen (e.g. on a now-disconnected second monitor), it is discarded on startup. `Preferences -> Reset window position` re-centers manually.
- **Configuration** is stored under the standard per-OS location by default:
  - Windows: `%APPDATA%\Manipulator\config.json`
  - macOS: `~/Library/Application Support/Manipulator/config.json`
  - Linux: `$XDG_CONFIG_HOME/Manipulator/config.json` (defaults to `~/.config/Manipulator/config.json`)

  Enable `Preferences -> Store config next to executable` to write `config.json` next to the running binary instead. A portable config takes precedence over the standard one on startup, so this is suitable for USB-stick installs or any setup where you want settings to travel with the app.

## Limitations

- Tk's menu bar and native dialogs (`filedialog`, `messagebox`) follow the OS theme, not sv-ttk - in dark mode you will have a dark window body with a light menu/dialog. This is a Tk limitation.
- macOS is not part of the release pipeline. The code runs there fine; build from source if you want a `.app`.
