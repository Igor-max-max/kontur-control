import os
import re
import sqlite3
import threading
import tkinter as tk
from datetime import date, datetime
from tkinter import filedialog, messagebox, ttk

import server
from mailer import send_due_emails, valid_email


COLORS = {
    "bg": "#f2f0e8",
    "paper": "#fffefa",
    "ink": "#13231d",
    "muted": "#607069",
    "green": "#174f3c",
    "lime": "#c7dd75",
    "red": "#a43d35",
}

RISK_FILTERS = {
    "Все уровни": None,
    "Срок нарушен": "overdue",
    "Критический": "critical",
    "Высокий": "high",
    "Средний": "medium",
    "Низкий": "low",
    "Исполнено": "done",
}


def display_date(value):
    return date.fromisoformat(value).strftime("%d.%m.%Y")


def input_date(value):
    try:
        return datetime.strptime(value, "%d.%m.%Y").date().isoformat()
    except ValueError:
        return date.fromisoformat(value).isoformat()


MINISTRY_NAME = r"(?:Минобрнауки\s+ЛНР|Министерство\s+образования\s+и\s+науки\s+(?:ЛНР|Луганской\s+Народной\s+Республики))"
MINISTRY_PREFIX = re.compile(rf"^\s*{MINISTRY_NAME}\s*[,;:/—–-]\s*", re.IGNORECASE)
MINISTRY_SUFFIX = re.compile(rf"(?:\s*[,;:/—–-]\s*|\s+){MINISTRY_NAME}\s*$", re.IGNORECASE)
MINISTRY_PARENS = re.compile(rf"\s*\(\s*{MINISTRY_NAME}\s*\)\s*$", re.IGNORECASE)


def display_assignee(value):
    name = MINISTRY_PREFIX.sub("", value)
    name = MINISTRY_SUFFIX.sub("", name)
    name = MINISTRY_PARENS.sub("", name)
    return name.strip() or value


class KonturApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Контур - контроль поручений")
        self.geometry("1180x720")
        self.minsize(900, 600)
        self.configure(bg=COLORS["bg"])
        self.smtp_config = None
        self.option_add("*Font", ("Segoe UI", 10))
        self._configure_styles()
        self._build_header()
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        self._build_control_tab()
        self._build_metrics_tab()
        self.refresh()

    def _configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10), font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", foreground=[("selected", COLORS["green"])])
        style.configure("Treeview", font=("Segoe UI", 13), rowheight=48, background=COLORS["paper"], fieldbackground=COLORS["paper"])
        style.configure("Treeview.Heading", font=("Segoe UI", 11, "bold"), background="#e8e9e1")
        style.configure("Primary.TButton", foreground="white", background=COLORS["green"], padding=(15, 9))
        style.map("Primary.TButton", background=[("active", "#0d3b2b")])
        style.configure("Secondary.TButton", foreground=COLORS["green"], padding=(15, 9))

    def _build_header(self):
        header = tk.Frame(self, bg=COLORS["green"], height=102)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="КОНТУР", bg=COLORS["green"], fg="white", font=("Segoe UI", 22, "bold")).pack(side="left", padx=(28, 15))
        tk.Label(header, text="От письма до исполнения\nКонтроль поручений Минобрнауки ЛНР", justify="left", bg=COLORS["green"], fg="#dce9e2", font=("Segoe UI", 11)).pack(side="left")
        tk.Label(header, text="БЕЗОПАСНЫЙ РЕЖИМ", bg=COLORS["lime"], fg=COLORS["green"], font=("Segoe UI", 9, "bold"), padx=12, pady=7).pack(side="right", padx=28)

    def _build_control_tab(self):
        tab = tk.Frame(self.notebook, bg=COLORS["bg"])
        self.notebook.add(tab, text="Контроль поручений")
        self.summary = tk.Frame(tab, bg=COLORS["bg"])
        self.summary.pack(fill="x", pady=(20, 16))
        self.summary_labels = {}
        for key, title in (("active", "В работе"), ("overdue", "Срок нарушен"), ("critical", "Критический риск"), ("reminders", "Напоминаний")):
            card = tk.Frame(self.summary, bg=COLORS["paper"], highlightbackground="#d9ddd3", highlightthickness=1)
            card.pack(side="left", fill="x", expand=True, padx=5)
            tk.Label(card, text=title, bg=COLORS["paper"], fg=COLORS["muted"]).pack(anchor="w", padx=16, pady=(12, 0))
            label = tk.Label(card, text="0", bg=COLORS["paper"], fg=COLORS["ink"], font=("Georgia", 28))
            label.pack(anchor="w", padx=16, pady=(0, 12))
            self.summary_labels[key] = label
        actions = tk.Frame(tab, bg=COLORS["bg"])
        actions.pack(fill="x", pady=(0, 12))
        ttk.Button(actions, text="Импорт Excel", command=self.import_excel, style="Secondary.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Добавить поручение", command=self.add_assignment, style="Secondary.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Отметить исполненным", command=self.complete_selected, style="Secondary.TButton").pack(side="left")
        ttk.Button(actions, text="Email исполнителя", command=self.edit_email, style="Secondary.TButton").pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Сформировать напоминания", command=self.run_reminders, style="Primary.TButton").pack(side="right")
        self.email_button = ttk.Button(actions, text="Отправить email", command=self.configure_email, style="Primary.TButton")
        self.email_button.pack(side="right", padx=(0, 8))
        edit_actions = tk.Frame(tab, bg=COLORS["bg"])
        edit_actions.pack(fill="x", pady=(0, 10))
        ttk.Button(edit_actions, text="Редактировать выбранное", command=self.edit_selected, style="Secondary.TButton").pack(side="left", padx=(0, 8))
        ttk.Button(edit_actions, text="Удалить выбранное", command=self.delete_selected, style="Secondary.TButton").pack(side="left")
        ttk.Button(edit_actions, text="Очистить содержимое", command=self.clear_assignments, style="Secondary.TButton").pack(side="left", padx=(8, 0))
        filters = tk.Frame(tab, bg=COLORS["bg"])
        filters.pack(fill="x", pady=(0, 10))
        tk.Label(filters, text="Исполнитель:", bg=COLORS["bg"]).pack(side="left")
        self.assignee_filter = ttk.Combobox(filters, width=25)
        self.assignee_filter.pack(side="left", padx=(6, 14))
        tk.Label(filters, text="Риск:", bg=COLORS["bg"]).pack(side="left")
        self.risk_filter = ttk.Combobox(filters, values=list(RISK_FILTERS), state="readonly", width=17)
        self.risk_filter.set("Все уровни")
        self.risk_filter.pack(side="left", padx=(6, 12))
        tk.Label(filters, text="Срок с:", bg=COLORS["bg"]).pack(side="left")
        self.date_from = ttk.Entry(filters, width=13)
        self.date_from.pack(side="left", padx=(6, 12))
        tk.Label(filters, text="по:", bg=COLORS["bg"]).pack(side="left")
        self.date_to = ttk.Entry(filters, width=13)
        self.date_to.pack(side="left", padx=(6, 12))
        ttk.Button(filters, text="Применить", command=self.apply_filters).pack(side="left", padx=(0, 5))
        ttk.Button(filters, text="Сбросить", command=self.reset_filters).pack(side="left")
        legend = tk.Frame(tab, bg=COLORS["bg"])
        legend.pack(fill="x", pady=(0, 10))
        tk.Label(legend, text="Уровень риска:", bg=COLORS["bg"], fg=COLORS["muted"], font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))
        for title, background, foreground in (
            ("Срок нарушен", "#f7d6d2", "#7d1f18"),
            ("Критический", "#ffe0b2", "#8a4b00"),
            ("Высокий", "#dcefdc", "#205c32"),
        ):
            tk.Label(legend, text=title, bg=background, fg=foreground, padx=9, pady=3, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 6))
        columns = ("number", "assignee", "due", "risk", "status")
        self.tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        headings = ("Документ", "Исполнитель", "Срок", "Риск", "Статус")
        widths = (240, 280, 155, 180, 150)
        for column, heading, width in zip(columns, headings, widths):
            self.tree.heading(column, text=heading, anchor="center")
            self.tree.column(column, width=width, minwidth=80, anchor="center")
        self.tree.tag_configure("overdue", background="#f7d6d2", foreground="#7d1f18")
        self.tree.tag_configure("critical", background="#ffe0b2", foreground="#8a4b00")
        self.tree.tag_configure("high", background="#dcefdc", foreground="#205c32")
        scrollbar = ttk.Scrollbar(tab, orient="vertical", command=self.tree.yview)
        self._grid_lines = []
        self._grid_refresh_pending = False
        self._grid_after_id = None

        def on_scroll(first, last):
            scrollbar.set(first, last)
            self._schedule_grid_refresh()

        self.tree.configure(yscrollcommand=on_scroll)
        self.tree.bind("<Configure>", lambda event: self._schedule_grid_refresh())
        self.tree.bind("<Destroy>", self._cancel_grid_refresh)
        self.tree.bind("<B1-Motion>", lambda event: self._schedule_grid_refresh(), add="+")
        self.tree.bind("<ButtonRelease-1>", lambda event: self._schedule_grid_refresh(), add="+")
        self.tree.bind("<Double-1>", lambda event: self.edit_selected() if self.tree.identify_row(event.y) else None)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        tk.Label(tab, text="Импорт .xlsx/.xlsm: номер, исполнитель, срок; email исполнителя — необязательная колонка. Отправка только после подтверждения.", bg=COLORS["bg"], fg=COLORS["muted"]).pack(anchor="w", pady=8)

    def _build_metrics_tab(self):
        tab = tk.Frame(self.notebook, bg=COLORS["bg"])
        self.notebook.add(tab, text="Показатели пилота")
        tk.Label(tab, text="Значения вносятся только после фактического замера. Вымышленные цифры не используются.", bg="#e6eef0", fg=COLORS["ink"], padx=15, pady=12).pack(fill="x", pady=(20, 14))
        self.metric_frame = tk.Frame(tab, bg=COLORS["bg"])
        self.metric_frame.pack(fill="both", expand=True)
        self.metric_entries = {}
        with server.connect() as db:
            metrics = db.execute("SELECT * FROM metrics ORDER BY rowid").fetchall()
        for row_index, metric in enumerate(metrics):
            row = tk.Frame(self.metric_frame, bg=COLORS["paper"], padx=14, pady=11, highlightbackground="#d9ddd3", highlightthickness=1)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=metric["title"], width=48, anchor="w", bg=COLORS["paper"], fg=COLORS["ink"], font=("Segoe UI", 10, "bold")).pack(side="left")
            baseline = ttk.Entry(row, width=14)
            baseline.pack(side="left", padx=5)
            current = ttk.Entry(row, width=14)
            current.pack(side="left", padx=5)
            if metric["baseline"] is not None:
                baseline.insert(0, str(metric["baseline"]))
            if metric["current"] is not None:
                current.insert(0, str(metric["current"]))
            ttk.Button(row, text="Сохранить", command=lambda item=metric["id"]: self.save_metric(item)).pack(side="right")
            self.metric_entries[metric["id"]] = (baseline, current)

    def refresh(self):
        with server.connect() as db:
            rows = db.execute("SELECT * FROM assignments ORDER BY due_date, id").fetchall()
            reminders = db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
        self.all_rows = rows
        names = sorted({display_assignee(row["assignee"]) for row in rows}, key=str.casefold)
        self.assignee_filter["values"] = names
        self.apply_filters(show_errors=False)
        counts = {"active": 0, "overdue": 0, "critical": 0, "reminders": reminders}
        for row in rows:
            risk = server.risk_for(row["due_date"], row["status"])
            if row["status"] != "Исполнено":
                counts["active"] += 1
                if risk["code"] in counts:
                    counts[risk["code"]] += 1
        for key, value in counts.items():
            self.summary_labels[key].configure(text=str(value), fg=COLORS["red"] if key in ("overdue", "critical") and value else COLORS["ink"])

    def apply_filters(self, show_errors=True):
        try:
            start = server.normalize_excel_date(self.date_from.get()) if self.date_from.get().strip() else None
            end = server.normalize_excel_date(self.date_to.get()) if self.date_to.get().strip() else None
            if start and end and start > end:
                raise ValueError("Начальная дата позже конечной")
        except ValueError:
            if show_errors:
                messagebox.showerror("Фильтр срока", "Укажите даты в формате ДД.ММ.ГГГГ или ГГГГ-ММ-ДД; дата «с» не должна быть позже «по».")
            return
        name = self.assignee_filter.get().strip().casefold()
        risk_code = RISK_FILTERS[self.risk_filter.get()]
        self.visible_rows = [row for row in self.all_rows
                             if (not name or name in display_assignee(row["assignee"]).casefold())
                             and (not start or row["due_date"] >= start)
                             and (not end or row["due_date"] <= end)
                             and (risk_code is None or server.risk_for(row["due_date"], row["status"])["code"] == risk_code)]
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self.visible_rows:
            risk = server.risk_for(row["due_date"], row["status"])
            number = row["doc_number"] + (" [ДЕМО]" if row["is_demo"] else "")
            self.tree.insert("", "end", iid=str(row["id"]), tags=(risk["code"],), values=(number, display_assignee(row["assignee"]), display_date(row["due_date"]), risk["label"], row["status"]))
        self._schedule_grid_refresh()

    def _schedule_grid_refresh(self):
        if not self._grid_refresh_pending:
            self._grid_refresh_pending = True
            self._grid_after_id = self.tree.after_idle(self._draw_grid_lines)

    def _cancel_grid_refresh(self, event):
        if event.widget is self.tree and self._grid_after_id is not None:
            self.tree.after_cancel(self._grid_after_id)
            self._grid_after_id = None
            self._grid_refresh_pending = False

    def _draw_grid_lines(self):
        self._grid_refresh_pending = False
        self._grid_after_id = None
        tree = self.tree
        if not tree.winfo_exists():
            return
        width, height = tree.winfo_width(), tree.winfo_height()
        if width <= 1 or height <= 1:
            return
        row_height = int(ttk.Style(self).lookup("Treeview", "rowheight"))
        borders = []
        y = 0
        while y < height:
            item = tree.identify_row(y)
            bounds = tree.bbox(item) if item else ()
            if bounds:
                bottom = bounds[1] + bounds[3]
                if 0 < bottom < height:
                    borders.append(bottom - 1)
                y = max(y + 1, bottom + 1)
            else:
                y += max(1, row_height // 2)
        borders = sorted(set(borders))
        separators = []
        first_visible = tree.identify_row(max(0, height // 2))
        for index, column in enumerate(tree["columns"][:-1], start=1):
            bounds = tree.bbox(first_visible, f"#{index}") if first_visible else ()
            position = bounds[0] + bounds[2] if bounds else sum(int(tree.column(c, "width")) for c in tree["columns"][:index])
            if 0 < position < width:
                separators.append(position - 1)
        needed = len(borders) + len(separators)
        while len(self._grid_lines) < needed:
            self._grid_lines.append(tk.Frame(tree, bg="#b8c2b9", borderwidth=0))
        for line, bottom in zip(self._grid_lines, borders):
            line.place(x=0, y=bottom, width=width, height=1)
        for line, left in zip(self._grid_lines[len(borders):], separators):
            line.place(x=left, y=0, width=1, height=height)
        for line in self._grid_lines[needed:]:
            line.place_forget()

    def reset_filters(self):
        self.assignee_filter.set("")
        self.risk_filter.set("Все уровни")
        self.date_from.delete(0, "end")
        self.date_to.delete(0, "end")
        self.apply_filters()

    def import_excel(self):
        path = filedialog.askopenfilename(title="Выберите книгу Excel", filetypes=(("Книга Excel", "*.xlsx *.xlsm"), ("Все файлы", "*.*")))
        if not path:
            return
        try:
            with open(path, "rb") as file:
                rows = server.parse_excel_rows(file.read(), path)
            imported = skipped = 0
            with server.connect() as db:
                for item in rows:
                    try:
                        db.execute("INSERT INTO assignments (doc_number, assignee, assignee_email, department, due_date, status, source) VALUES (?, ?, ?, ?, ?, ?, 'Excel')", (item["doc_number"], item["assignee"], item["assignee_email"], item["department"], item["due_date"], item["status"]))
                        imported += 1
                    except sqlite3.IntegrityError:
                        skipped += 1
            self.refresh()
            messagebox.showinfo("Импорт завершён", f"Добавлено: {imported}\nПропущено дублей: {skipped}")
        except Exception as error:
            messagebox.showerror("Ошибка импорта", str(error))

    def add_assignment(self):
        dialog = tk.Toplevel(self)
        dialog.title("Новое поручение")
        dialog.transient(self)
        dialog.grab_set()
        entries = {}
        fields = (("doc_number", "Номер документа"), ("assignee", "Исполнитель"), ("assignee_email", "Email исполнителя (необязательно)"), ("department", "Подразделение"), ("due_date", "Срок (ДД.ММ.ГГГГ)"))
        for row, (key, label) in enumerate(fields):
            tk.Label(dialog, text=label).grid(row=row, column=0, sticky="w", padx=15, pady=8)
            entry = ttk.Entry(dialog, width=38)
            entry.grid(row=row, column=1, padx=15, pady=8)
            entries[key] = entry
        entries["doc_number"].focus_set()

        def save():
            values = {key: entry.get().strip() for key, entry in entries.items()}
            if not all(values[key] for key in ("doc_number", "assignee", "department", "due_date")):
                messagebox.showerror("Ошибка", "Заполните номер, исполнителя, подразделение и срок", parent=dialog)
                return
            if values["assignee_email"] and not valid_email(values["assignee_email"]):
                messagebox.showerror("Ошибка", "Некорректный email исполнителя", parent=dialog)
                return
            try:
                values["due_date"] = input_date(values["due_date"])
                with server.connect() as db:
                    db.execute("INSERT INTO assignments (doc_number, assignee, assignee_email, department, due_date) VALUES (?, ?, ?, ?, ?)", tuple(values.values()))
            except ValueError:
                messagebox.showerror("Ошибка", "Используйте формат даты ДД.ММ.ГГГГ", parent=dialog)
                return
            except sqlite3.IntegrityError:
                messagebox.showerror("Ошибка", "Такой номер документа уже существует", parent=dialog)
                return
            dialog.destroy()
            self.refresh()

        ttk.Button(dialog, text="Поставить на контроль", command=save, style="Primary.TButton").grid(row=len(fields), column=0, columnspan=2, sticky="ew", padx=15, pady=15)

    def complete_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Нет выбора", "Выберите поручение в таблице")
            return
        with server.connect() as db:
            db.execute("UPDATE assignments SET status = 'Исполнено' WHERE id = ?", (int(selected[0]),))
        self.refresh()

    def edit_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Нет выбора", "Выберите поручение в таблице")
            return
        assignment_id = int(selected[0])
        with server.connect() as db:
            row = db.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if row is None:
            self.refresh()
            messagebox.showwarning("Поручение не найдено", "Список был обновлён")
            return
        dialog = tk.Toplevel(self)
        dialog.title("Редактирование поручения")
        dialog.transient(self)
        dialog.grab_set()
        entries = {}
        fields = (("doc_number", "Номер документа"), ("assignee", "Исполнитель"),
                  ("assignee_email", "Email исполнителя"), ("department", "Подразделение"),
                  ("due_date", "Срок (ДД.ММ.ГГГГ)"))
        for index, (key, label) in enumerate(fields):
            tk.Label(dialog, text=label).grid(row=index, column=0, sticky="w", padx=15, pady=7)
            entry = ttk.Entry(dialog, width=42)
            entry.insert(0, display_date(row[key]) if key == "due_date" else display_assignee(row[key]) if key == "assignee" else row[key])
            entry.grid(row=index, column=1, padx=15, pady=7)
            entries[key] = entry
        tk.Label(dialog, text="Статус").grid(row=len(fields), column=0, sticky="w", padx=15, pady=7)
        status = ttk.Combobox(dialog, values=("В работе", "Исполнено"), state="readonly", width=39)
        status.set(row["status"])
        status.grid(row=len(fields), column=1, padx=15, pady=7)
        entries["doc_number"].focus_set()

        def save():
            values = {key: entry.get().strip() for key, entry in entries.items()}
            if not all(values[key] for key in ("doc_number", "assignee", "department", "due_date")):
                messagebox.showerror("Ошибка", "Заполните номер, исполнителя, подразделение и срок", parent=dialog)
                return
            if values["assignee_email"] and not valid_email(values["assignee_email"]):
                messagebox.showerror("Ошибка", "Некорректный email исполнителя", parent=dialog)
                return
            try:
                values["due_date"] = input_date(values["due_date"])
            except ValueError:
                messagebox.showerror("Ошибка", "Используйте формат даты ДД.ММ.ГГГГ", parent=dialog)
                return
            try:
                with server.connect() as db:
                    cursor = db.execute(
                        "UPDATE assignments SET doc_number = ?, assignee = ?, assignee_email = ?, department = ?, due_date = ?, status = ? WHERE id = ?",
                        (*values.values(), status.get(), assignment_id),
                    )
                    if cursor.rowcount == 0:
                        messagebox.showwarning("Поручение не найдено", "Список был обновлён", parent=dialog)
                        dialog.destroy()
                        self.refresh()
                        return
            except sqlite3.IntegrityError:
                messagebox.showerror("Ошибка", "Такой номер документа уже существует", parent=dialog)
                return
            dialog.destroy()
            self.refresh()

        ttk.Button(dialog, text="Сохранить изменения", command=save, style="Primary.TButton").grid(
            row=len(fields) + 1, column=0, columnspan=2, sticky="ew", padx=15, pady=15)

    def delete_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Нет выбора", "Выберите поручение в таблице")
            return
        assignment_id = int(selected[0])
        with server.connect() as db:
            row = db.execute("SELECT doc_number FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if row is None:
            self.refresh()
            messagebox.showwarning("Поручение не найдено", "Список был обновлён")
            return
        if not messagebox.askyesno("Удаление поручения", f"Удалить поручение {row['doc_number']} и связанные записи журнала напоминаний?", parent=self):
            return
        with server.connect() as db:
            db.execute("DELETE FROM reminders WHERE assignment_id = ?", (assignment_id,))
            db.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
        self.refresh()

    def clear_assignments(self):
        with server.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0]
        if count == 0:
            messagebox.showinfo("Очистка реестра", "Таблица поручений уже пуста")
            return
        if not messagebox.askyesno(
            "Очистить содержимое",
            f"Удалить все поручения ({count}) и связанные записи журнала напоминаний?\nЭто действие нельзя отменить.",
            parent=self,
        ):
            return
        with server.connect() as db:
            db.execute("DELETE FROM reminders")
            db.execute("DELETE FROM assignments")
        self.refresh()
        self.reset_filters()

    def edit_email(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Нет выбора", "Выберите поручение для указания email")
            return
        assignment_id = int(selected[0])
        with server.connect() as db:
            row = db.execute("SELECT assignee, assignee_email FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        dialog = tk.Toplevel(self)
        dialog.title("Адрес исполнителя")
        dialog.transient(self)
        dialog.grab_set()
        tk.Label(dialog, text=f"{display_assignee(row['assignee'])} — email для выбранного поручения").pack(padx=20, pady=(20, 8))
        address = ttk.Entry(dialog, width=48)
        address.insert(0, row["assignee_email"])
        address.pack(padx=20)
        address.focus_set()

        def save():
            email = address.get().strip()
            if email and not valid_email(email):
                messagebox.showerror("Ошибка", "Укажите корректный email или оставьте поле пустым", parent=dialog)
                return
            with server.connect() as db:
                db.execute("UPDATE assignments SET assignee_email = ? WHERE id = ?", (email, assignment_id))
            dialog.destroy()
            self.refresh()

        ttk.Button(dialog, text="Сохранить", command=save, style="Primary.TButton").pack(pady=18)

    def configure_email(self):
        eligible = [row for row in self.visible_rows if not row["is_demo"] and row["status"] != "Исполнено"
                    and server.risk_for(row["due_date"], row["status"])["code"] != "low"
                    and row["assignee_email"]]
        if not eligible:
            messagebox.showinfo("Email-уведомления", "Среди показанных поручений нет подходящих: нужен email исполнителя и срок в пределах 7 дней либо просрочка. Демоданные не рассылаются.")
            return
        dialog = tk.Toplevel(self)
        dialog.title("Параметры SMTP")
        dialog.transient(self)
        dialog.grab_set()
        defaults = self.smtp_config or {
            "host": os.getenv("CONTROL_SMTP_HOST", ""), "port": os.getenv("CONTROL_SMTP_PORT", "587"),
            "security": os.getenv("CONTROL_SMTP_SECURITY", "STARTTLS"), "sender": os.getenv("CONTROL_SMTP_FROM", ""),
            "username": os.getenv("CONTROL_SMTP_USER", ""), "password": os.getenv("CONTROL_SMTP_PASSWORD", ""),
        }
        entries = {}
        fields = (("host", "SMTP-сервер"), ("port", "Порт"), ("sender", "Адрес отправителя"),
                  ("username", "Логин (если нужен)"), ("password", "Пароль (если нужен)"))
        for index, (key, label) in enumerate(fields):
            tk.Label(dialog, text=label).grid(row=index, column=0, sticky="w", padx=15, pady=7)
            entry = ttk.Entry(dialog, width=42, show="*" if key == "password" else "")
            entry.insert(0, defaults.get(key, ""))
            entry.grid(row=index, column=1, padx=15, pady=7)
            entries[key] = entry
        tk.Label(dialog, text="Шифрование").grid(row=5, column=0, sticky="w", padx=15, pady=7)
        security = ttk.Combobox(dialog, state="readonly", values=("STARTTLS", "SSL"), width=39)
        security.set(defaults.get("security", "STARTTLS"))
        security.grid(row=5, column=1, padx=15, pady=7)
        tk.Label(dialog, text=f"Показано поручений с email и ближайшим сроком: {len(eligible)}.\n"
                              "Письмо содержит только номер, срок, статус и исполнителя.\n"
                              "Пароль хранится только до закрытия приложения.", justify="left").grid(
                                  row=6, column=0, columnspan=2, sticky="w", padx=15, pady=10)

        def send():
            config = {key: entry.get().strip() for key, entry in entries.items()}
            config["security"] = security.get()
            if not config["host"] or not valid_email(config["sender"]):
                messagebox.showerror("SMTP", "Укажите SMTP-сервер и корректный адрес отправителя", parent=dialog)
                return
            try:
                port = int(config["port"])
                if not 1 <= port <= 65535:
                    raise ValueError()
            except ValueError:
                messagebox.showerror("SMTP", "Укажите корректный номер порта", parent=dialog)
                return
            if bool(config["username"]) != bool(config["password"]):
                messagebox.showerror("SMTP", "Укажите и логин, и пароль либо оставьте оба поля пустыми", parent=dialog)
                return
            if not messagebox.askyesno("Подтвердите отправку", f"Отправить уведомления по {len(eligible)} показанным поручениям?\nПовторные письма за сегодня будут пропущены.", parent=dialog):
                return
            self.smtp_config = config
            dialog.destroy()
            self.email_button.configure(state="disabled")
            ids = [row["id"] for row in eligible]

            def worker():
                try:
                    result = send_due_emails(ids, config)
                except Exception as error:
                    self.after(0, lambda: self.email_finished(None, str(error)))
                else:
                    self.after(0, lambda: self.email_finished(result))

            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(dialog, text="Отправить уведомления", command=send, style="Primary.TButton").grid(
            row=7, column=0, columnspan=2, sticky="ew", padx=15, pady=15)

    def email_finished(self, result, error=None):
        self.email_button.configure(state="normal")
        self.refresh()
        if error:
            messagebox.showerror("Ошибка отправки", error)
            return
        summary = f"Отправлено: {result['sent']}\nПропущено: {result['skipped']}\nОшибок: {len(result['errors'])}"
        if result["errors"]:
            messagebox.showwarning("Результат отправки", summary + "\n\n" + "\n".join(result["errors"][:8]))
        else:
            messagebox.showinfo("Результат отправки", summary)

    def run_reminders(self):
        created = 0
        today_prefix = date.today().isoformat() + "%"
        now = datetime.now().replace(microsecond=0).isoformat()
        with server.connect() as db:
            rows = db.execute("SELECT * FROM assignments WHERE status != 'Исполнено'").fetchall()
            for row in rows:
                risk = server.risk_for(row["due_date"], row["status"])
                if risk["code"] == "low" or db.execute("SELECT 1 FROM reminders WHERE assignment_id = ? AND channel = 'Протокол' AND sent_at LIKE ?", (row["id"], today_prefix)).fetchone():
                    continue
                db.execute("INSERT INTO reminders (assignment_id, level, sent_at) VALUES (?, ?, ?)", (row["id"], risk["code"], now))
                created += 1
        self.refresh()
        messagebox.showinfo("Напоминания", f"Сформировано новых напоминаний: {created}\nВнешняя рассылка в MVP не выполняется.")

    def save_metric(self, metric_id):
        baseline, current = self.metric_entries[metric_id]
        try:
            values = [float(value) if value else None for value in (baseline.get().strip(), current.get().strip())]
        except ValueError:
            messagebox.showerror("Ошибка", "Значения показателей должны быть числами")
            return
        with server.connect() as db:
            db.execute("UPDATE metrics SET baseline = ?, current = ?, measured_at = ? WHERE id = ?", (*values, date.today().isoformat(), metric_id))
        messagebox.showinfo("Показатель", "Значения сохранены")

if __name__ == "__main__":
    try:
        server.init_db()
        KonturApp().mainloop()
    except Exception as error:
        messagebox.showerror("Контур: ошибка запуска", str(error))
