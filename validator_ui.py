from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from validation_logic import (
    FileResult,
    TEXTURE_SUFFIXES,
    _find_mayapy,
    add_prefix_to_file,
    collect_errors_by_category,
    fixed_filename,
    file_type_for_path,
    list_submission_files,
    validate_folder,
)


class SubmissionValidator(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Submission Folder Validator")
        self.geometry("1050x620")
        self.minsize(760, 420)
        self._results_queue: queue.Queue[tuple[str, Path, int, object]] = queue.Queue()
        self._selected_root: Path | None = None
        self._generation = 0
        self._operation_running = False
        self._validate_after_file_reload = False
        self._last_results: list[FileResult] = []
        self._fixed_files: dict[str, str] = {}
        self._build_ui()
        self.after(100, self._poll_results)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        folder_row = ttk.Frame(container)
        folder_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(folder_row, text="Source Art folder:").pack(side=tk.LEFT)
        self.folder_var = tk.StringVar()
        self.folder_entry = ttk.Entry(folder_row, textvariable=self.folder_var)
        self.folder_entry.pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=8
        )
        self.folder_entry.bind("<Return>", self._on_folder_enter)
        self.browse_button = ttk.Button(
            folder_row,
            text="Browse...",
            command=self._browse_folder,
        )
        self.browse_button.pack(side=tk.LEFT)

        self.mayapy_var = tk.StringVar(value=_find_mayapy())
        ttk.Button(
            folder_row, text="Settings", command=self._open_settings
        ).pack(side=tk.LEFT, padx=(8, 0))

        table_frame = ttk.Frame(container)
        table_frame.pack(fill=tk.BOTH, expand=True)
        columns = ("type", "status", "details", "action")
        tree_style = ttk.Style(self)
        tree_style.configure("Submission.Treeview", rowheight=32)
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="tree headings",
            style="Submission.Treeview",
        )
        self.tree.heading("#0", text="File")
        self.tree.heading("type", text="Type")
        self.tree.heading("status", text="Status")
        self.tree.heading("details", text="Details")
        self.tree.heading("action", text="Action")
        self.tree.column("#0", width=250, minwidth=150)
        self.tree.column("type", width=80, minwidth=70, stretch=False)
        self.tree.column("status", width=70, minwidth=65, stretch=False)
        self.tree.column("details", width=450, minwidth=180, stretch=False)
        self.tree.column("action", width=170, minwidth=170, stretch=False, anchor=tk.W)
        self.tree.tag_configure("pass", background="#d9ead3")
        self.tree.tag_configure("fail", background="#f4cccc")
        self.tree.tag_configure("skip", background="#e6e6e6")
        self.tree.tag_configure("ready", background="#eaf2f8")

        vertical_scroll = ttk.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.tree.yview
        )
        horizontal_scroll = ttk.Scrollbar(
            table_frame, orient=tk.HORIZONTAL, command=self.tree.xview
        )
        self._vertical_scroll = vertical_scroll
        self._horizontal_scroll = horizontal_scroll
        self._table_frame = table_frame
        self._view_buttons: dict[str, ttk.Button] = {}
        self._delete_buttons: dict[str, ttk.Button] = {}
        self._prefix_fix_buttons: dict[str, ttk.Button] = {}
        self._prefix_fixes: dict[str, str] = {}
        self.tree.configure(
            yscrollcommand=self._on_tree_yview,
            xscrollcommand=self._on_tree_xview,
        )
        self.tree.bind("<Configure>", self._schedule_layout_updates)
        self.tree.bind("<MouseWheel>", self._schedule_delete_button_layout, add="+")
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical_scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        self.after_idle(self._refresh_tree_scrollbars)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.summary_var = tk.StringVar(value="Select a folder to validate.")
        ttk.Label(container, textvariable=self.summary_var).pack(
            anchor=tk.W, pady=(8, 4)
        )
        progress_frame = ttk.Frame(container)
        progress_frame.pack(fill=tk.X, pady=(0, 8))
        self.progress_var = tk.StringVar()
        ttk.Label(progress_frame, textvariable=self.progress_var, width=24).pack(
            side=tk.LEFT
        )
        self.progress_bar = ttk.Progressbar(
            progress_frame,
            mode="determinate",
            maximum=1,
        )
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.progress_frame = progress_frame
        self.progress_frame.pack_forget()
        footer = ttk.Frame(container)
        footer.pack(fill=tk.X, pady=(0, 0))
        self.validate_button = ttk.Button(
            footer, text="Validate", command=self._start_validation, width=20
        )
        self.validate_button.pack(side=tk.RIGHT)
        self.fix_all_button = ttk.Button(
            footer,
            text="Fix All",
            command=self._fix_all_prefixes,
            width=20,
            state="disabled",
        )
        self.fix_all_button.pack(side=tk.RIGHT, padx=(0, 8))
        self.summary_button = ttk.Button(
            footer, text="Summary", command=self._show_error_summary, width=20
        )
        self.footer = footer

    def _browse_folder(self) -> None:
        selected = filedialog.askdirectory(title="Select submission folder")
        if selected:
            self.folder_var.set(selected)
            self._load_folder(selected)

    def _on_folder_enter(self, _event: tk.Event) -> None:
        self._load_folder(self.folder_var.get())

    def _load_folder(
        self,
        folder_text: str,
        *,
        preserve_fixed_files: bool = False,
        validate_after_reload: bool = False,
    ) -> None:
        if not folder_text.strip() or not Path(folder_text).is_dir():
            messagebox.showerror("Invalid folder", "Select an existing source art folder.")
            return

        root = Path(folder_text).resolve()
        self._selected_root = root
        self._generation += 1
        generation = self._generation
        self._last_results = []
        if not preserve_fixed_files:
            self._fixed_files.clear()
        self._validate_after_file_reload = validate_after_reload
        self.summary_button.pack_forget()
        self.fix_all_button.configure(state="disabled")
        self.tree.delete(*self.tree.get_children())
        self._clear_action_buttons()
        self._prefix_fixes.clear()
        self.validate_button.configure(state="disabled")
        self.browse_button.configure(state="disabled")
        self.folder_entry.configure(state="disabled")
        loading_message = (
            "Reloading file list..."
            if validate_after_reload
            else "Loading file list..."
        )
        self.summary_var.set(loading_message)
        self._begin_operation(self.summary_var.get())
        threading.Thread(
            target=self._file_list_thread,
            args=(root, generation),
            daemon=True,
        ).start()

    def _file_list_thread(self, root: Path, generation: int) -> None:
        try:
            files = list_submission_files(root)
        except OSError as error:
            self._results_queue.put(("scan_error", root, generation, str(error)))
            return
        self._results_queue.put(("file_list", root, generation, files))

    def _open_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Settings")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding=12)
        content.pack(fill=tk.BOTH, expand=True)
        ttk.Label(content, text="mayapy executable:").grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 6)
        )
        path_var = tk.StringVar(value=self.mayapy_var.get())
        path_entry = ttk.Entry(content, textvariable=path_var, width=58)
        path_entry.grid(row=1, column=0, sticky=tk.EW)

        def browse() -> None:
            selected = filedialog.askopenfilename(
                parent=dialog,
                title="Select mayapy executable",
                filetypes=(("mayapy executable", "mayapy.exe"), ("All files", "*.*")),
            )
            if selected:
                path_var.set(selected)

        ttk.Button(content, text="Browse...", command=browse).grid(
            row=1, column=1, padx=(8, 0)
        )

        buttons = ttk.Frame(content)
        buttons.grid(row=2, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)

        def save_settings() -> None:
            self.mayapy_var.set(path_var.get().strip())
            dialog.destroy()

        ttk.Button(buttons, text="Save", command=save_settings).pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        path_entry.focus_set()

    def _start_validation(
        self, status_message: str = "Validating files... Maya QC may take a few minutes."
    ) -> None:
        folder_text = self.folder_var.get().strip()
        if not folder_text or not Path(folder_text).is_dir():
            messagebox.showerror(
                "Invalid folder", "Select an existing source art folder."
            )
            return

        root = Path(folder_text).resolve()
        if root != self._selected_root:
            self._load_folder(folder_text)
            return

        generation = self._generation
        mayapy = self.mayapy_var.get().strip()
        self._last_results = []
        self.summary_button.pack_forget()
        self.fix_all_button.configure(state="disabled")
        self._clear_action_buttons()
        self._prefix_fixes.clear()
        self.summary_var.set(status_message)
        self._begin_operation(status_message)
        threading.Thread(
            target=self._validation_thread,
            args=(root, generation, mayapy),
            daemon=True,
        ).start()

    def _validation_thread(self, root: Path, generation: int, mayapy: str) -> None:
        try:
            results = validate_folder(
                root,
                mayapy,
                lambda completed, total, status: self._results_queue.put(
                    (
                        "progress",
                        root,
                        generation,
                        (completed, total, status),
                    )
                ),
            )
        except OSError as error:
            self._results_queue.put(
                ("validation_error", root, generation, str(error))
            )
            return
        self._results_queue.put(("results", root, generation, results))

    def _begin_operation(self, status: str) -> None:
        self._operation_running = True
        self.validate_button.configure(state="disabled")
        self.fix_all_button.configure(state="disabled")
        self.browse_button.configure(state="disabled")
        self.folder_entry.configure(state="disabled")
        for button in (
            *self._delete_buttons.values(),
            *self._prefix_fix_buttons.values(),
        ):
            button.configure(state="disabled")
        self.progress_frame.pack(fill=tk.X, pady=(0, 8), before=self.footer)
        self.progress_var.set(status)
        self.progress_bar.stop()
        self.progress_bar.configure(mode="indeterminate", maximum=100, value=0)
        self.progress_bar.start(12)

    def _update_progress(self, completed: int, total: int, status: str) -> None:
        self.progress_var.set(status)
        if total <= 0:
            if str(self.progress_bar["mode"]) != "indeterminate":
                self.progress_bar.stop()
                self.progress_bar.configure(mode="indeterminate", value=0)
                self.progress_bar.start(12)
            return

        self.progress_bar.stop()
        self.progress_bar.configure(
            mode="determinate",
            maximum=max(total, 1),
            value=min(completed, total),
        )

    def _finish_operation(self) -> None:
        self._operation_running = False
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate", maximum=1, value=0)
        self.progress_var.set("")
        self.progress_frame.pack_forget()
        self.validate_button.configure(state="normal")
        self.browse_button.configure(state="normal")
        self.folder_entry.configure(state="normal")
        for button in (
            *self._delete_buttons.values(),
            *self._prefix_fix_buttons.values(),
        ):
            button.configure(state="normal")

    def _poll_results(self) -> None:
        while True:
            try:
                result_type, root, generation, payload = (
                    self._results_queue.get_nowait()
                )
            except queue.Empty:
                break

            if root != self._selected_root or generation != self._generation:
                continue

            if result_type == "progress":
                completed, total, status = payload
                self._update_progress(completed, total, status)
            elif result_type == "fix_progress":
                completed, total = payload
                self._update_progress(
                    completed,
                    total,
                    f"Fixing filenames... {completed}/{total}",
                )
            elif result_type == "fix_results":
                renamed, failures, fixed_files = payload
                self._fixed_files.update(fixed_files)
                if failures:
                    messagebox.showwarning(
                        "Fix Results",
                        f"Renamed: {renamed}\nCould not rename: {len(failures)}\n\n"
                        + "\n".join(failures),
                        parent=self,
                    )
                if renamed:
                    self._load_folder(
                        str(root),
                        preserve_fixed_files=True,
                        validate_after_reload=True,
                    )
                else:
                    self._finish_operation()
                    self._update_summary_after_delete()
            elif result_type == "scan_error":
                self._finish_operation()
                self.validate_button.configure(state="normal")
                self.summary_var.set("Could not load the file list.")
                messagebox.showerror("Folder scan error", str(payload))
            elif result_type == "file_list":
                self._finish_operation()
                files = payload
                assert isinstance(files, list)
                for path in files:
                    relative_path = str(path.relative_to(root))
                    file_type = file_type_for_path(path)
                    self.tree.insert(
                        "",
                        tk.END,
                        text=relative_path,
                        values=(file_type, "READY", "Awaiting validation"),
                        tags=("ready",),
                    )
                should_validate = self._validate_after_file_reload
                self._validate_after_file_reload = False
                if should_validate:
                    self._start_validation("Fixing the file naming")
                else:
                    self.validate_button.configure(state="normal")
                    self.summary_var.set(
                        f"Files found: {len(files)}    Select Validate to run checks."
                    )
            elif result_type == "validation_error":
                self._finish_operation()
                self.summary_var.set("Validation could not complete.")
                messagebox.showerror("Validation error", str(payload))
            elif result_type == "results":
                self._finish_operation()
                results = payload
                assert isinstance(results, list)
                self._last_results = results
                counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
                self._clear_action_buttons()
                self._prefix_fixes.clear()
                self.tree.delete(*self.tree.get_children())
                for result in results:
                    assert isinstance(result, FileResult)
                    counts[result.status] += 1
                    tag = result.status.lower()
                    item_id = self.tree.insert(
                        "",
                        tk.END,
                        text=result.relative_path,
                        values=(
                            result.file_type,
                            result.status,
                            result.details,
                            "",
                        ),
                        tags=(tag,),
                    )
                    if result.prefix_to_add:
                        self._prefix_fixes[item_id] = result.prefix_to_add
                self.fix_all_button.configure(
                    state="normal" if self._prefix_fixes else "disabled"
                )
                self.summary_var.set(
                    f"Files: {len(results)}    Passed: {counts['PASS']}    "
                    f"Failed: {counts['FAIL']}    Skipped: {counts['SKIP']}    "
                    f"Fixed: {len(self._fixed_files)}"
                )
                self.summary_button.pack(side=tk.LEFT)
                self._schedule_layout_updates()

        self.after(100, self._poll_results)

    def _on_tree_yview(self, first: str, last: str) -> None:
        self._sync_scrollbar(self._vertical_scroll, first, last)
        self._schedule_delete_button_layout()

    def _on_tree_xview(self, first: str, last: str) -> None:
        self._sync_scrollbar(self._horizontal_scroll, first, last)
        self._schedule_delete_button_layout()

    @staticmethod
    def _sync_scrollbar(scrollbar: ttk.Scrollbar, first: str, last: str) -> None:
        scrollbar.set(first, last)
        if float(first) > 0.0 or float(last) < 1.0:
            if scrollbar.winfo_manager() != "grid":
                scrollbar.grid()
        elif scrollbar.winfo_manager() == "grid":
            scrollbar.grid_remove()

    def _refresh_tree_scrollbars(self) -> None:
        first, last = self.tree.yview()
        self._on_tree_yview(str(first), str(last))
        first, last = self.tree.xview()
        self._on_tree_xview(str(first), str(last))

    def _schedule_delete_button_layout(self, _event: tk.Event | None = None) -> None:
        self.after_idle(self._layout_delete_buttons)

    def _schedule_layout_updates(self, _event: tk.Event | None = None) -> None:
        self._schedule_delete_button_layout()

    def _show_detail_log(self, item_id: str) -> None:
        detail_text = self._format_detail_log(self.tree.set(item_id, "details"))
        if not detail_text:
            return

        filename = self.tree.item(item_id, "text")
        dialog = tk.Toplevel(self)
        dialog.title(f"Details - {Path(filename).name}")
        dialog.transient(self)
        dialog.geometry("720x420")
        dialog.minsize(480, 260)

        content = ttk.Frame(dialog, padding=12)
        content.pack(fill=tk.BOTH, expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        detail_view = tk.Text(content, wrap=tk.WORD, undo=False)
        detail_scroll = ttk.Scrollbar(
            content, orient=tk.VERTICAL, command=detail_view.yview
        )
        detail_view.configure(
            yscrollcommand=lambda first, last: self._sync_scrollbar(
                detail_scroll, first, last
            )
        )
        detail_view.grid(row=0, column=0, sticky="nsew")
        detail_scroll.grid(row=0, column=1, sticky="ns")
        detail_view.insert("1.0", detail_text)
        detail_view.configure(state=tk.DISABLED)
        first, last = detail_view.yview()
        self._sync_scrollbar(detail_scroll, str(first), str(last))
        ttk.Button(content, text="Close", command=dialog.destroy).grid(
            row=1, column=0, columnspan=2, sticky=tk.E, pady=(10, 0)
        )
        dialog.grab_set()

    @staticmethod
    def _format_detail_log(detail_text: str) -> str:
        return "\n".join(
            reason.strip()
            for reason in detail_text.split("; ")
            if reason.strip()
        )

    def _show_error_summary(self) -> None:
        errors_by_category = collect_errors_by_category(self._last_results)
        dialog = tk.Toplevel(self)
        dialog.title("Validation Summary")
        dialog.transient(self)
        dialog.geometry("520x440")
        dialog.minsize(380, 280)

        content = ttk.Frame(dialog, padding=12)
        content.pack(fill=tk.BOTH, expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        error_view = tk.Text(content, wrap=tk.WORD, undo=False)
        error_scroll = ttk.Scrollbar(
            content, orient=tk.VERTICAL, command=error_view.yview
        )
        error_view.configure(
            yscrollcommand=lambda first, last: self._sync_scrollbar(
                error_scroll, first, last
            )
        )
        for category, error_names in errors_by_category.items():
            error_view.insert(tk.END, f"{category}\n", "heading")
            if error_names:
                for error_name in error_names:
                    error_view.insert(tk.END, f"  • {error_name}\n")
            else:
                error_view.insert(tk.END, "  No errors\n")
            error_view.insert(tk.END, "\n")
        error_view.insert(tk.END, "Fixed files\n", "heading")
        fixed_file_log = self._format_fixed_file_log(self._fixed_files)
        if fixed_file_log:
            error_view.insert(tk.END, fixed_file_log)
        else:
            error_view.insert(tk.END, "  No files fixed\n")
        error_view.tag_configure("heading", font=("TkDefaultFont", 10, "bold"))
        error_view.configure(state=tk.DISABLED)
        error_view.grid(row=0, column=0, sticky="nsew")
        error_scroll.grid(row=0, column=1, sticky="ns")
        first, last = error_view.yview()
        self._sync_scrollbar(error_scroll, str(first), str(last))

        ttk.Button(content, text="Close", command=dialog.destroy).grid(
            row=1, column=0, columnspan=2, sticky=tk.E, pady=(10, 0)
        )
        dialog.grab_set()

    @staticmethod
    def _format_fixed_file_log(fixed_files: dict[str, str]) -> str:
        return "".join(
            f"  {original} -> {fixed}\n"
            for original, fixed in fixed_files.items()
        )

    def _layout_delete_buttons(self) -> None:
        visible_delete_items: set[str] = set()
        visible_prefix_items: set[str] = set()
        tree_x = self.tree.winfo_x()
        tree_y = self.tree.winfo_y()
        for item_id in self.tree.get_children():
            status = self.tree.set(item_id, "status")
            file_type = self.tree.set(item_id, "type")
            is_validated = status != "READY"
            has_delete = is_validated and file_type == "Other"
            has_prefix_fix = is_validated and item_id in self._prefix_fixes
            bounds = self.tree.bbox(item_id, "action")
            if not bounds:
                continue

            x, y, width, height = bounds
            button_x = x
            if is_validated:
                view_width = 68
                self._place_action_button(
                    item_id,
                    "View",
                    self._view_buttons,
                    lambda row_id=item_id: self._show_detail_log(row_id),
                    tree_x + button_x,
                    tree_y + y,
                    view_width,
                    height,
                )
                button_x += view_width
            if has_prefix_fix:
                prefix_width = 76
                self._place_action_button(
                    item_id,
                    "Fix Name",
                    self._prefix_fix_buttons,
                    lambda row_id=item_id: self._fix_missing_prefix(row_id),
                    tree_x + button_x,
                    tree_y + y,
                    prefix_width,
                    height,
                )
                button_x += prefix_width
            if has_delete:
                self._place_action_button(
                    item_id,
                    "Delete",
                    self._delete_buttons,
                    lambda row_id=item_id: self._delete_unsupported_file(row_id),
                    tree_x + button_x,
                    tree_y + y,
                    68,
                    height,
                )
                visible_delete_items.add(item_id)
            if has_prefix_fix:
                visible_prefix_items.add(item_id)

        for item_id, button in self._delete_buttons.items():
            if item_id not in visible_delete_items:
                button.place_forget()
        for item_id, button in self._prefix_fix_buttons.items():
            if item_id not in visible_prefix_items:
                button.place_forget()

        visible_view_items = set(self.tree.get_children())
        for item_id, button in self._view_buttons.items():
            if (
                item_id not in visible_view_items
                or self.tree.set(item_id, "status") == "READY"
                or not self.tree.bbox(item_id, "action")
            ):
                button.place_forget()

    def _place_action_button(
        self,
        item_id: str,
        label: str,
        buttons: dict[str, ttk.Button],
        command: Callable[[], None],
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        button = buttons.get(item_id)
        if button is None:
            button = ttk.Button(self._table_frame, text=label, command=command)
            buttons[item_id] = button
        button.place(
            x=x,
            y=y,
            width=width,
            height=height,
        )

    def _clear_action_buttons(self) -> None:
        for buttons in (
            self._view_buttons,
            self._delete_buttons,
            self._prefix_fix_buttons,
        ):
            for button in buttons.values():
                button.destroy()
            buttons.clear()

    def _fix_missing_prefix(self, item_id: str) -> None:
        if self._operation_running:
            return
        if self._selected_root is None:
            messagebox.showerror("Rename failed", "No submission folder is selected.")
            return

        prefix = self._prefix_fixes.get(item_id)
        if not prefix or self.tree.set(item_id, "status") == "READY":
            return

        relative_path = Path(self.tree.item(item_id, "text"))
        texture_suffix = self._confirm_filename_fix(relative_path, prefix)
        if texture_suffix is None:
            return

        root = self._selected_root
        generation = self._generation
        fixes = [(relative_path, prefix, texture_suffix or None)]
        self.summary_var.set("Fixing the file naming")
        self._begin_operation("Fixing filenames...")
        threading.Thread(
            target=self._filename_fix_thread,
            args=(root, generation, fixes),
            daemon=True,
        ).start()

    def _confirm_filename_fix(
        self, relative_path: Path, prefix: str
    ) -> str | None:
        if prefix != "T_":
            target_name = fixed_filename(relative_path, prefix)
            confirmed = messagebox.askyesno(
                "Confirm filename fix",
                f"Rename this file to:\n\n{target_name}\n\n"
                "The filename will be lowercased, texture keyword removed, "
                "and required prefix applied.",
                parent=self,
            )
            return "" if confirmed else None

        dialog = tk.Toplevel(self)
        dialog.title("Fix texture filename")
        dialog.transient(self)
        dialog.resizable(False, False)
        dialog.grab_set()

        content = ttk.Frame(dialog, padding=12)
        content.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            content,
            text="Enter the texture type suffix for this file:",
        ).pack(anchor=tk.W)

        suffix_var = tk.StringVar()
        suffix_entry = ttk.Entry(
            content,
            textvariable=suffix_var,
            width=24,
        )
        suffix_entry.pack(fill=tk.X, pady=(6, 8))
        ttk.Label(
            content,
            text="Required: albedo, normal, or roughness.",
        ).pack(anchor=tk.W)
        target_var = tk.StringVar(value="New filename: enter a suffix")
        ttk.Label(
            content,
            textvariable=target_var,
            wraplength=360,
        ).pack(anchor=tk.W, pady=(8, 0))

        def update_preview(*_args: object) -> None:
            suffix = suffix_var.get().strip().lower()
            if suffix in TEXTURE_SUFFIXES:
                target_var.set(
                    f"New filename: "
                    f"{fixed_filename(relative_path, prefix, suffix)}"
                )
            else:
                target_var.set("New filename: enter a supported suffix")

        suffix_var.trace_add("write", update_preview)
        selected: dict[str, str | None] = {"suffix": None}

        def confirm() -> None:
            suffix = suffix_var.get().strip().lower()
            if suffix not in TEXTURE_SUFFIXES:
                messagebox.showwarning(
                    "Texture type required",
                    "Enter albedo, normal, or roughness before renaming this file.",
                    parent=dialog,
                )
                return
            selected["suffix"] = suffix
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        buttons = ttk.Frame(content)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=cancel).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Rename", command=confirm).pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Return>", lambda _event: confirm())
        self._center_dialog_over_app(dialog)
        suffix_entry.focus_set()
        self.wait_window(dialog)
        return selected["suffix"]

    def _center_dialog_over_app(self, dialog: tk.Toplevel) -> None:
        self.update_idletasks()
        dialog.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_reqheight()) // 2
        dialog.geometry(f"+{x}+{y}")

    def _fix_all_prefixes(self) -> None:
        if self._operation_running:
            return
        if self._selected_root is None:
            return

        fixes = [
            (item_id, prefix)
            for item_id, prefix in self._prefix_fixes.items()
            if prefix and self.tree.set(item_id, "status") != "READY"
        ]
        if not fixes:
            return

        if not messagebox.askyesno(
            "Fix All Filenames",
            f"Fix the filenames of {len(fixes)} file(s)?\n\n"
            "Names will be lowercased, texture keywords removed, and required "
            "prefixes applied. Choose a suffix for each texture. "
            "Unsupported files will not be deleted.",
            parent=self,
        ):
            return

        texture_suffixes: dict[str, str] = {}
        for item_id, prefix in fixes:
            if prefix != "T_":
                continue
            relative_path = Path(self.tree.item(item_id, "text"))
            suffix = self._confirm_filename_fix(relative_path, prefix)
            if suffix is None:
                return
            texture_suffixes[item_id] = suffix

        filename_fixes = [
            (
                Path(self.tree.item(item_id, "text")),
                prefix,
                texture_suffixes.get(item_id),
            )
            for item_id, prefix in fixes
        ]
        root = self._selected_root
        generation = self._generation
        self.summary_var.set("Fixing the file naming")
        self._begin_operation("Fixing filenames...")
        threading.Thread(
            target=self._filename_fix_thread,
            args=(root, generation, filename_fixes),
            daemon=True,
        ).start()

    def _filename_fix_thread(
        self,
        root: Path,
        generation: int,
        fixes: list[tuple[Path, str, str | None]],
    ) -> None:
        renamed = 0
        failures: list[str] = []
        fixed_files: dict[str, str] = {}
        for index, (relative_path, prefix, texture_suffix) in enumerate(
            fixes, start=1
        ):
            try:
                target = add_prefix_to_file(
                    root,
                    relative_path,
                    prefix,
                    texture_suffix,
                )
            except (OSError, ValueError) as error:
                failures.append(f"{relative_path.name}: {error}")
            else:
                renamed += 1
                fixed_files[str(relative_path)] = str(
                    target.relative_to(root)
                )
            self._results_queue.put(
                ("fix_progress", root, generation, (index, len(fixes)))
            )
        self._results_queue.put(
            (
                "fix_results",
                root,
                generation,
                (renamed, failures, fixed_files),
            )
        )

    def _delete_unsupported_file(self, item_id: str) -> None:
        if self._selected_root is None:
            messagebox.showerror("Delete failed", "No submission folder is selected.")
            return

        relative_path = Path(self.tree.item(item_id, "text"))
        candidate = self._selected_root / relative_path
        try:
            resolved_path = candidate.resolve(strict=True)
            resolved_path.relative_to(self._selected_root)
            if not candidate.is_file():
                messagebox.showerror("Delete failed", "The selected path is not a file.")
                return
        except (OSError, ValueError) as error:
            messagebox.showerror(
                "Delete failed",
                f"The file is missing or outside the selected folder:\n{error}",
            )
            return

        if not messagebox.askyesno(
            "Delete redundant file",
            f"Delete this unsupported file?\n\n{relative_path}",
            parent=self,
        ):
            return

        try:
            candidate.unlink()
        except OSError as error:
            messagebox.showerror("Delete failed", f"Could not delete file:\n{error}")
            return

        self.tree.delete(item_id)
        self._prefix_fixes.pop(item_id, None)
        button = self._delete_buttons.pop(item_id, None)
        if button:
            button.destroy()
        button = self._prefix_fix_buttons.pop(item_id, None)
        if button:
            button.destroy()
        self._schedule_delete_button_layout()
        self._update_summary_after_delete()

    def _update_summary_after_delete(self) -> None:
        counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
        for item_id in self.tree.get_children():
            status = self.tree.set(item_id, "status")
            if status in counts:
                counts[status] += 1
        total = len(self.tree.get_children())
        self.summary_var.set(
            f"Files: {total}    Passed: {counts['PASS']}    "
            f"Failed: {counts['FAIL']}    Skipped: {counts['SKIP']}    "
            f"Fixed: {len(self._fixed_files)}"
        )
