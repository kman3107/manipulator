"""
Manipulator - bulk filename editor with regex filtering.

The "path" input accepts either a directory (in which case every non-directory
child is a candidate) or a single file path. A regex filter narrows the
candidate set; the "Invert" toggle keeps only files that do NOT match.
Exactly one of four rename operations runs against the resulting set:

    Append   - insert text before the file extension
    Prepend  - insert text at the start of the filename
    Remove   - regex sub of the pattern with the empty string
    Replace  - regex sub of the pattern with a replacement

Preview lists the planned changes without touching anything. Apply renames
on disk and refuses to overwrite an existing file.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from tkinter import (
    BooleanVar,
    END,
    IntVar,
    Menu,
    StringVar,
    filedialog,
    messagebox,
    ttk,
)

import sv_ttk
from tkinterdnd2 import DND_FILES, TkinterDnD


def _dist_version(name: str) -> str:
    try:
        return pkg_version(name)
    except PackageNotFoundError:
        return "unknown"


APP_NAME = "Manipulator"
DEFAULT_W, DEFAULT_H = 820, 600
OP_APPEND, OP_PREPEND, OP_REMOVE, OP_REPLACE = 1, 2, 3, 4


def _parse_geometry(geom: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"^(\d+)x(\d+)([+-])(-?\d+)([+-])(-?\d+)$", geom)
    if not m:
        return None
    return int(m[1]), int(m[2]), int(m[4]), int(m[6])


def _virtual_screen(root) -> tuple[int, int, int, int]:
    """Return (x, y, width, height) of the virtual desktop spanning all
    connected monitors. On Windows we use Win32 directly because
    winfo_screenwidth() only reports the primary monitor; on macOS/Linux
    we fall back to Tk, which already reports the spanning extent."""
    if sys.platform == "win32":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
            SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
            x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
            if w > 0 and h > 0:
                return x, y, w, h
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _geometry_on_screen(root, geom: str, min_visible: int = 40) -> bool:
    """A geometry is recoverable only if the title bar can be grabbed:
    its top edge sits on the visible desktop, and at least `min_visible`
    pixels of it are horizontally on-screen."""
    parsed = _parse_geometry(geom)
    if not parsed:
        return False
    w, _h, x, y = parsed
    vx, vy, vw, vh = _virtual_screen(root)
    # Tolerance handles Windows' maximize border which can report y just
    # above the virtual top (e.g. y == -8).
    if y < vy - 12 or y > vy + vh - min_visible:
        return False
    visible_w = min(x + w, vx + vw) - max(x, vx)
    if visible_w < min_visible:
        return False
    return True


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def standard_config_path() -> Path:
    return config_dir() / "config.json"


def portable_config_path() -> Path:
    # When frozen, "next to the executable" means next to the .exe in the
    # PyInstaller --onedir output. Otherwise it's next to main.py.
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent
    return base / "config.json"


def load_config() -> tuple[dict, bool]:
    """Load config, preferring a portable file next to the executable if it
    exists. Returns (data, is_portable)."""
    portable = portable_config_path()
    if portable.is_file():
        try:
            return json.loads(portable.read_text(encoding="utf-8")), True
        except (OSError, ValueError):
            pass
    standard = standard_config_path()
    try:
        return json.loads(standard.read_text(encoding="utf-8")), False
    except (OSError, ValueError):
        return {}, False


def save_config(data: dict, portable: bool) -> None:
    if portable:
        target = portable_config_path()
    else:
        target = standard_config_path()
        # Remove a stale portable file so it can't shadow the standard one
        # the next time the app starts.
        try:
            portable_config_path().unlink(missing_ok=True)
        except OSError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class Rename:
    src: Path
    dst: Path


def normalize_path(text: str) -> Path:
    text = text.strip().strip('"').strip("'")
    if not text:
        raise ValueError("Path is empty.")
    return Path(text)


def gather_candidates(path: Path, recursive: bool) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        it = path.rglob("*") if recursive else path.iterdir()
        return sorted(
            (p for p in it if p.is_file()),
            key=lambda p: str(p).lower(),
        )
    raise FileNotFoundError(f"Path does not exist: {path}")


def apply_filter(
    items: list[Path], pattern: str, invert: bool, flags: int = 0
) -> list[Path]:
    if not pattern:
        return list(items)
    rx = re.compile(pattern, flags)
    if invert:
        return [p for p in items if not rx.search(p.name)]
    return [p for p in items if rx.search(p.name)]


def new_name(src: Path, op: int, a: str, b: str, flags: int = 0) -> str:
    name = src.name
    stem, ext = src.stem, src.suffix
    if op == OP_APPEND:
        return f"{stem}{a}{ext}"
    if op == OP_PREPEND:
        return f"{a}{name}"
    if op == OP_REMOVE:
        return re.sub(a, "", name, flags=flags) if a else name
    if op == OP_REPLACE:
        return re.sub(a, b, name, flags=flags) if a else name
    raise ValueError(f"Unknown operation: {op}")


def build_plan(
    items: list[Path], op: int, a: str, b: str, flags: int = 0
) -> list[Rename]:
    plan: list[Rename] = []
    for src in items:
        candidate = new_name(src, op, a, b, flags)
        if candidate and candidate != src.name:
            plan.append(Rename(src, src.with_name(candidate)))
    return plan


class App:
    def __init__(self, root: TkinterDnD.Tk) -> None:
        self.root = root
        root.title(APP_NAME)
        root.geometry(f"{DEFAULT_W}x{DEFAULT_H}")
        root.minsize(700, 500)

        self.path_var = StringVar()
        self.filter_var = StringVar()
        self.filter_invert = BooleanVar(value=False)
        self.case_insensitive = BooleanVar(value=False)
        self.recursive = BooleanVar(value=False)
        self.append_var = StringVar()
        self.prepend_var = StringVar()
        self.remove_var = StringVar()
        self.replace_old_var = StringVar()
        self.replace_new_var = StringVar()
        self.op_var = IntVar(value=OP_APPEND)
        self.status_var = StringVar(value="Ready.")

        self.pref_remember_position = BooleanVar(value=False)
        self.pref_remember_inputs = BooleanVar(value=False)
        self.pref_dark_mode = BooleanVar(value=False)
        self.pref_portable_config = BooleanVar(value=False)

        self._cfg, is_portable = load_config()
        self.pref_remember_position.set(bool(self._cfg.get("remember_position", False)))
        self.pref_remember_inputs.set(bool(self._cfg.get("remember_inputs", False)))
        self.pref_dark_mode.set(bool(self._cfg.get("dark_mode", False)))
        self.pref_portable_config.set(is_portable)

        self._apply_theme()
        self.pref_dark_mode.trace_add("write", lambda *_: self._apply_theme())

        self._build_menu()
        self._build_ui()
        self._apply_remembered_state()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_theme(self) -> None:
        sv_ttk.set_theme("dark" if self.pref_dark_mode.get() else "light")

    def _build_menu(self) -> None:
        menubar = Menu(self.root)
        self.root.config(menu=menubar)

        prefs = Menu(menubar, tearoff=0)
        prefs.add_checkbutton(
            label="Dark mode",
            variable=self.pref_dark_mode,
        )
        prefs.add_separator()
        prefs.add_checkbutton(
            label="Remember window position",
            variable=self.pref_remember_position,
        )
        prefs.add_checkbutton(
            label="Remember last used settings",
            variable=self.pref_remember_inputs,
        )
        prefs.add_checkbutton(
            label="Store config next to executable",
            variable=self.pref_portable_config,
        )
        prefs.add_separator()
        prefs.add_command(
            label="Reset window position",
            command=self._reset_window_position,
        )
        menubar.add_cascade(label="Preferences", menu=prefs)

        helpm = Menu(menubar, tearoff=0)
        helpm.add_command(label=f"About {APP_NAME}", command=self._show_about)
        menubar.add_cascade(label="Help", menu=helpm)

    def _show_about(self) -> None:
        tk_ver = self.root.tk.call("info", "patchlevel")
        message = (
            f"{APP_NAME}\n\n"
            "A small utility for bulk filename editing with regex filtering. "
            "Point it at a file or directory, optionally narrow the set with a "
            "regex, and apply one of four operations: append, prepend, "
            "regex remove, or regex replace. Preview shows the planned changes "
            "before anything touches disk.\n\n"
            "Built with:\n"
            f"  - Python {platform.python_version()}\n"
            f"  - Tcl/Tk {tk_ver} (tkinter / ttk)\n"
            f"  - tkinterdnd2 {_dist_version('tkinterdnd2')}\n"
            f"  - sv-ttk {_dist_version('sv-ttk')}"
        )
        messagebox.showinfo(f"About {APP_NAME}", message)

    def _reset_window_position(self) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - DEFAULT_W) // 2)
        y = max(0, (sh - DEFAULT_H) // 2)
        self.root.geometry(f"{DEFAULT_W}x{DEFAULT_H}+{x}+{y}")

    def _apply_remembered_state(self) -> None:
        if self.pref_remember_position.get():
            geom = self._cfg.get("geometry")
            if isinstance(geom, str) and geom and _geometry_on_screen(self.root, geom):
                try:
                    self.root.geometry(geom)
                except Exception:
                    pass
        if self.pref_remember_inputs.get():
            inputs = self._cfg.get("inputs") or {}
            self.path_var.set(str(inputs.get("path", "")))
            self.filter_var.set(str(inputs.get("filter", "")))
            self.filter_invert.set(bool(inputs.get("filter_invert", False)))
            self.case_insensitive.set(bool(inputs.get("case_insensitive", False)))
            self.recursive.set(bool(inputs.get("recursive", False)))
            try:
                self.op_var.set(int(inputs.get("op", OP_APPEND)))
            except (TypeError, ValueError):
                self.op_var.set(OP_APPEND)
            self.append_var.set(str(inputs.get("append", "")))
            self.prepend_var.set(str(inputs.get("prepend", "")))
            self.remove_var.set(str(inputs.get("remove", "")))
            self.replace_old_var.set(str(inputs.get("replace_old", "")))
            self.replace_new_var.set(str(inputs.get("replace_new", "")))

    def _on_close(self) -> None:
        cfg: dict = {
            "remember_position": self.pref_remember_position.get(),
            "remember_inputs": self.pref_remember_inputs.get(),
            "dark_mode": self.pref_dark_mode.get(),
            "portable_config": self.pref_portable_config.get(),
        }
        if self.pref_remember_position.get():
            cfg["geometry"] = self.root.geometry()
        if self.pref_remember_inputs.get():
            cfg["inputs"] = {
                "path": self.path_var.get(),
                "filter": self.filter_var.get(),
                "filter_invert": self.filter_invert.get(),
                "case_insensitive": self.case_insensitive.get(),
                "recursive": self.recursive.get(),
                "op": self.op_var.get(),
                "append": self.append_var.get(),
                "prepend": self.prepend_var.get(),
                "remove": self.remove_var.get(),
                "replace_old": self.replace_old_var.get(),
                "replace_new": self.replace_new_var.get(),
            }
        try:
            save_config(cfg, portable=self.pref_portable_config.get())
        except OSError:
            pass
        self.root.destroy()

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="Path:").grid(row=0, column=0, sticky="w", **pad)
        path_entry = ttk.Entry(outer, textvariable=self.path_var)
        path_entry.grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(outer, text="Browse...", command=self._on_browse).grid(
            row=0, column=2, sticky="ew", **pad
        )
        path_entry.drop_target_register(DND_FILES)
        path_entry.dnd_bind("<<Drop>>", self._on_drop_path)

        ttk.Label(outer, text="Filter (regex):").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(outer, textvariable=self.filter_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Checkbutton(outer, text="Invert", variable=self.filter_invert).grid(
            row=1, column=2, sticky="w", **pad
        )

        opts = ttk.Frame(outer)
        opts.grid(row=2, column=1, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(opts, text="Case insensitive", variable=self.case_insensitive).pack(
            side="left"
        )
        ttk.Checkbutton(opts, text="Recursive", variable=self.recursive).pack(
            side="left", padx=(12, 0)
        )

        ttk.Separator(outer, orient="horizontal").grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=8
        )

        ops = ttk.LabelFrame(outer, text="Operation", padding=8)
        ops.grid(row=4, column=0, columnspan=3, sticky="ew", **pad)
        ops.columnconfigure(1, weight=1)
        ops.columnconfigure(3, weight=1)

        ttk.Radiobutton(
            ops, text="Append:", variable=self.op_var, value=OP_APPEND
        ).grid(row=0, column=0, sticky="w", **pad)
        append_e = ttk.Entry(ops, textvariable=self.append_var)
        append_e.grid(row=0, column=1, columnspan=3, sticky="ew", **pad)

        ttk.Radiobutton(
            ops, text="Prepend:", variable=self.op_var, value=OP_PREPEND
        ).grid(row=1, column=0, sticky="w", **pad)
        prepend_e = ttk.Entry(ops, textvariable=self.prepend_var)
        prepend_e.grid(row=1, column=1, columnspan=3, sticky="ew", **pad)

        ttk.Radiobutton(
            ops, text="Remove (regex):", variable=self.op_var, value=OP_REMOVE
        ).grid(row=2, column=0, sticky="w", **pad)
        remove_e = ttk.Entry(ops, textvariable=self.remove_var)
        remove_e.grid(row=2, column=1, columnspan=3, sticky="ew", **pad)

        ttk.Radiobutton(
            ops, text="Replace (regex):", variable=self.op_var, value=OP_REPLACE
        ).grid(row=3, column=0, sticky="w", **pad)
        replace_old_e = ttk.Entry(ops, textvariable=self.replace_old_var)
        replace_old_e.grid(row=3, column=1, sticky="ew", **pad)
        ttk.Label(ops, text="->").grid(row=3, column=2, **pad)
        replace_new_e = ttk.Entry(ops, textvariable=self.replace_new_var)
        replace_new_e.grid(row=3, column=3, sticky="ew", **pad)

        for entry, op in (
            (append_e, OP_APPEND),
            (prepend_e, OP_PREPEND),
            (remove_e, OP_REMOVE),
            (replace_old_e, OP_REPLACE),
            (replace_new_e, OP_REPLACE),
        ):
            entry.bind("<FocusIn>", lambda _e, v=op: self.op_var.set(v))

        actions = ttk.Frame(outer)
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", **pad)
        for i in range(3):
            actions.columnconfigure(i, weight=1)
        ttk.Button(actions, text="Preview", command=self._on_preview).grid(
            row=0, column=0, sticky="ew", padx=4
        )
        ttk.Button(actions, text="Apply", command=self._on_apply).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ttk.Button(actions, text="Clear", command=self._on_clear).grid(
            row=0, column=2, sticky="ew", padx=4
        )

        ttk.Separator(outer, orient="horizontal").grid(
            row=6, column=0, columnspan=3, sticky="ew", pady=8
        )

        ttk.Label(outer, textvariable=self.status_var, anchor="w").grid(
            row=7, column=0, columnspan=3, sticky="ew", **pad
        )

        list_frame = ttk.Frame(outer)
        list_frame.grid(row=8, column=0, columnspan=3, sticky="nsew", **pad)
        outer.rowconfigure(8, weight=1)
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            list_frame, columns=("old", "new"), show="headings", height=12
        )
        self.tree.heading("old", text="Current")
        self.tree.heading("new", text="After")
        self.tree.column("old", width=380, anchor="w")
        self.tree.column("new", width=380, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.tag_configure("collide", foreground="#c0392b")
        self.tree.tag_configure("ok", foreground="#27ae60")
        self.tree.tag_configure("err", foreground="#c0392b")

        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)
        self.tree.bind("<FocusOut>", lambda _e: self._clear_tree_selection())

    def _on_drop_path(self, event) -> str:
        # event.data is a Tcl list; splitlist handles {} wrapping around paths with spaces.
        paths = self.root.tk.splitlist(event.data)
        if paths:
            self.path_var.set(paths[0])
        return event.action

    def _on_browse(self) -> None:
        initial = self.path_var.get().strip() or str(Path.home())
        chosen = filedialog.askdirectory(title="Select working directory", initialdir=initial)
        if chosen:
            self.path_var.set(chosen)

    def _on_clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.status_var.set("Cleared.")

    def _clear_tree_selection(self) -> None:
        sel = self.tree.selection()
        if sel:
            self.tree.selection_remove(sel)

    def _on_tree_click(self, event) -> None:
        if self.tree.identify_row(event.y) == "":
            self._clear_tree_selection()

    def _args_for(self, op: int) -> tuple[str, str]:
        if op == OP_APPEND:
            return self.append_var.get(), ""
        if op == OP_PREPEND:
            return self.prepend_var.get(), ""
        if op == OP_REMOVE:
            return self.remove_var.get(), ""
        if op == OP_REPLACE:
            return self.replace_old_var.get(), self.replace_new_var.get()
        return "", ""

    def _collect_plan(self) -> tuple[list[Rename], list[Path], Path] | None:
        try:
            path = normalize_path(self.path_var.get())
            candidates = gather_candidates(path, self.recursive.get())
            flags = re.IGNORECASE if self.case_insensitive.get() else 0
            filtered = apply_filter(
                candidates,
                self.filter_var.get().strip(),
                self.filter_invert.get(),
                flags,
            )
            op = self.op_var.get()
            a, b = self._args_for(op)
            plan = build_plan(filtered, op, a, b, flags)
            return plan, filtered, path
        except re.error as e:
            messagebox.showerror("Regex error", str(e))
        except (ValueError, FileNotFoundError, OSError) as e:
            messagebox.showerror("Manipulator", str(e))
        return None

    def _on_preview(self) -> None:
        result = self._collect_plan()
        if result is None:
            return
        plan, filtered, root = result
        self._show_plan(plan, filtered, root)

    def _label(self, p: Path, root: Path) -> str:
        if root.is_dir():
            try:
                return str(p.relative_to(root))
            except ValueError:
                return str(p)
        return p.name

    @staticmethod
    def _is_collision(r: Rename) -> bool:
        if not r.dst.exists():
            return False
        try:
            return not r.dst.samefile(r.src)
        except OSError:
            return True

    def _show_plan(
        self, plan: list[Rename], filtered: list[Path], root: Path
    ) -> None:
        self.tree.delete(*self.tree.get_children())
        collisions = 0
        for r in plan:
            collide = self._is_collision(r)
            if collide:
                collisions += 1
            self.tree.insert(
                "",
                END,
                values=(self._label(r.src, root), self._label(r.dst, root)),
                tags=("collide",) if collide else (),
            )
        unchanged = len(filtered) - len(plan)
        self.status_var.set(
            f"{len(plan)} change(s) planned | {unchanged} unchanged | "
            f"{collisions} collision(s) | {len(filtered)} after filter"
        )

    def _on_apply(self) -> None:
        result = self._collect_plan()
        if result is None:
            return
        plan, filtered, root = result
        if not plan:
            self._show_plan(plan, filtered, root)
            self.status_var.set("Nothing to rename.")
            return
        collisions = [r for r in plan if self._is_collision(r)]
        if collisions:
            self._show_plan(plan, filtered, root)
            messagebox.showerror(
                "Collision",
                f"{len(collisions)} target name(s) already exist. Resolve before applying.",
            )
            return
        if not messagebox.askyesno("Apply changes", f"Rename {len(plan)} file(s)?"):
            return
        renamed, errors = self._apply_plan(plan)
        self._show_plan_after(plan, renamed, errors, root)
        if errors:
            messagebox.showwarning(
                "Manipulator",
                f"Renamed {len(renamed)}, {len(errors)} error(s). See list for details.",
            )
        else:
            self.status_var.set(f"Renamed {len(renamed)} file(s).")

    def _apply_plan(
        self, plan: list[Rename]
    ) -> tuple[list[Rename], list[tuple[Rename, str]]]:
        renamed: list[Rename] = []
        errors: list[tuple[Rename, str]] = []
        for r in plan:
            try:
                r.src.rename(r.dst)
                renamed.append(r)
            except OSError as e:
                errors.append((r, str(e)))
        return renamed, errors

    def _show_plan_after(
        self,
        plan: list[Rename],
        renamed: list[Rename],
        errors: list[tuple[Rename, str]],
        root: Path,
    ) -> None:
        self.tree.delete(*self.tree.get_children())
        done = {(r.src, r.dst) for r in renamed}
        err_map = {(r.src, r.dst): msg for r, msg in errors}
        for r in plan:
            key = (r.src, r.dst)
            src_label = self._label(r.src, root)
            dst_label = self._label(r.dst, root)
            if key in done:
                self.tree.insert("", END, values=(src_label, dst_label), tags=("ok",))
            elif key in err_map:
                self.tree.insert(
                    "",
                    END,
                    values=(src_label, f"{dst_label}  [error: {err_map[key]}]"),
                    tags=("err",),
                )


def main() -> int:
    root = TkinterDnD.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
