import os
import re
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image
from tkinterdnd2 import DND_FILES, TkinterDnD

Image.MAX_IMAGE_PIXELS = None

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
selected_files = []

# -----------------------------
# Helpers
# -----------------------------
def sanitize_filename(name):
    name = name.strip()
    return re.sub(r'[<>:"/\\|?*]', "", name)


def format_bytes(size):
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def parse_dropped_files(data):
    files = root.tk.splitlist(data)
    valid_files = []

    for file_path in files:
        path = Path(file_path)
        if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file():
            valid_files.append(str(path))

    return valid_files


# -----------------------------
# Theme
# -----------------------------
LIGHT_THEME = {
    "BG": "#f5f7fb",
    "CARD": "#ffffff",
    "CARD_2": "#e9eef7",
    "TEXT": "#0f172a",
    "MUTED": "#5b6475",
    "ACCENT": "#2563eb",
    "ACCENT_HOVER": "#1d4ed8",
    "BORDER": "#d6deea",
    "INPUT_BG": "#ffffff",
    "LIST_BG": "#fbfcfe",
    "DROP_BG": "#f8fafc",
    "RESULT_BG": "#f8fafc",
    "PROGRESS_TROUGH": "#dde5f0",
}

DARK_THEME = {
    "BG": "#0f172a",
    "CARD": "#111827",
    "CARD_2": "#1f2937",
    "TEXT": "#f8fafc",
    "MUTED": "#94a3b8",
    "ACCENT": "#22c55e",
    "ACCENT_HOVER": "#16a34a",
    "BORDER": "#334155",
    "INPUT_BG": "#0b1220",
    "LIST_BG": "#0b1220",
    "DROP_BG": "#0b1220",
    "RESULT_BG": "#0b1220",
    "PROGRESS_TROUGH": "#1e293b",
}

theme_var = None
theme = LIGHT_THEME


def current_theme():
    return DARK_THEME if theme_var.get() else LIGHT_THEME


def apply_theme():
    global theme
    theme = current_theme()

    root.configure(bg=theme["BG"])
    main_container.configure(bg=theme["BG"])
    header_frame.configure(bg=theme["BG"])
    header_left.configure(bg=theme["BG"])
    app_title.configure(bg=theme["BG"], fg=theme["TEXT"])
    app_subtitle.configure(bg=theme["BG"], fg=theme["MUTED"])
    theme_toggle_wrap.configure(bg=theme["BG"])
    theme_label.configure(bg=theme["BG"], fg=theme["MUTED"])

    style.configure(
        "Modern.Horizontal.TProgressbar",
        troughcolor=theme["PROGRESS_TROUGH"],
        background=theme["ACCENT"],
        bordercolor=theme["PROGRESS_TROUGH"],
        lightcolor=theme["ACCENT"],
        darkcolor=theme["ACCENT"],
        thickness=16
    )

    style.configure(
        "Modern.TCombobox",
        fieldbackground=theme["INPUT_BG"],
        background=theme["CARD_2"],
        foreground=theme["TEXT"],
        arrowcolor=theme["TEXT"],
        bordercolor=theme["BORDER"],
        lightcolor=theme["BORDER"],
        darkcolor=theme["BORDER"]
    )

    style.map(
        "Modern.TCombobox",
        fieldbackground=[("readonly", theme["INPUT_BG"])],
        foreground=[("readonly", theme["TEXT"])],
    )

    content_frame.configure(bg=theme["BG"])

    for panel in [left_panel, right_panel]:
        panel.configure(bg=theme["CARD"], highlightbackground=theme["BORDER"])

    for frame in [left_inner, right_inner, button_row, files_section, file_list_frame,
                  preview_section, preview_frame, progress_wrap, action_row]:
        frame.configure(bg=theme["CARD"])

    for widget in [
        left_title, left_desc, selected_count_label, files_title,
        right_title, right_desc, preview_title, progress_text_label, status_label
    ]:
        widget.configure(bg=theme["CARD"], fg=theme["TEXT"] if widget in [left_title, selected_count_label, files_title, right_title, preview_title, status_label] else theme["MUTED"])

    drop_frame.configure(bg=theme["DROP_BG"], highlightbackground=theme["BORDER"])
    drop_title.configure(bg=theme["DROP_BG"], fg=theme["TEXT"])
    drop_subtitle.configure(bg=theme["DROP_BG"], fg=theme["MUTED"])

    settings_card.configure(bg=theme["DROP_BG"], highlightbackground=theme["BORDER"])
    results_box.configure(bg=theme["RESULT_BG"], highlightbackground=theme["BORDER"])

    # settings labels
    for lbl in settings_labels:
        lbl.configure(bg=theme["DROP_BG"], fg=theme["MUTED"])

    # result labels
    for lbl in result_key_labels:
        lbl.configure(bg=theme["RESULT_BG"], fg=theme["MUTED"])
    for lbl in result_value_labels:
        lbl.configure(bg=theme["RESULT_BG"], fg=theme["TEXT"])

    style_entry(width_entry)
    style_entry(height_entry)
    style_entry(rename_entry)
    style_entry(start_number_entry)

    style_listbox(file_listbox)
    style_listbox(preview_listbox)

    select_button.configure(
        bg=theme["CARD_2"], fg=theme["TEXT"],
        activebackground=theme["BORDER"], activeforeground=theme["TEXT"]
    )
    clear_button.configure(
        bg=theme["CARD_2"], fg=theme["TEXT"],
        activebackground=theme["BORDER"], activeforeground=theme["TEXT"]
    )
    optimize_button.configure(
        bg=theme["ACCENT"], fg="white",
        activebackground=theme["ACCENT_HOVER"], activeforeground="white"
    )

    theme_toggle.configure(
        bg=theme["BG"],
        activebackground=theme["BG"],
        selectcolor=theme["BG"],
        fg=theme["TEXT"]
    )


# -----------------------------
# Widget styling helpers
# -----------------------------
def style_entry(entry):
    entry.configure(
        bg=theme["INPUT_BG"],
        fg=theme["TEXT"],
        insertbackground=theme["TEXT"],
        relief="flat",
        highlightthickness=1,
        highlightbackground=theme["BORDER"],
        highlightcolor=theme["ACCENT"]
    )


def style_listbox(lb):
    lb.configure(
        bg=theme["LIST_BG"],
        fg=theme["TEXT"],
        selectbackground=theme["ACCENT"],
        selectforeground="white",
        relief="flat",
        highlightthickness=1,
        highlightbackground=theme["BORDER"],
        bd=0
    )


# -----------------------------
# UI update functions
# -----------------------------
def update_selected_label():
    count = len(selected_files)
    if count == 0:
        selected_count_label.config(text="No images selected")
    elif count == 1:
        selected_count_label.config(text="1 image selected")
    else:
        selected_count_label.config(text=f"{count} images selected")


def update_preview(*args):
    preview_listbox.delete(0, tk.END)

    if not selected_files:
        preview_listbox.insert(tk.END, "Your renamed optimized files will appear here.")
        return

    base_name = sanitize_filename(rename_entry.get())
    if not base_name:
        preview_listbox.insert(tk.END, "Enter a base file name to preview output names.")
        return

    try:
        start_number = int(start_number_entry.get())
        if start_number < 1:
            raise ValueError
    except ValueError:
        preview_listbox.insert(tk.END, "Starting number must be 1 or higher.")
        return

    output_format = format_var.get().lower()
    preview_limit = 100

    for index, _ in enumerate(selected_files[:preview_limit], start=start_number):
        preview_listbox.insert(tk.END, f"{base_name}-{index}.{output_format}")

    if len(selected_files) > preview_limit:
        preview_listbox.insert(tk.END, f"... and {len(selected_files) - preview_limit} more")


def refresh_file_table():
    file_listbox.delete(0, tk.END)

    if not selected_files:
        file_listbox.insert(tk.END, "No files loaded yet.")
        return

    for file_path in selected_files:
        file_listbox.insert(tk.END, os.path.basename(file_path))


def clear_results():
    result_before_value.config(text="-")
    result_after_value.config(text="-")
    result_saved_value.config(text="-")
    result_count_value.config(text="-")
    progress_bar["value"] = 0
    progress_bar["maximum"] = 100
    progress_text_label.config(text="0 / 0 completed")
    status_label.config(text="Ready")


# -----------------------------
# File actions
# -----------------------------
def select_images():
    global selected_files

    files = filedialog.askopenfilenames(
        title="Select image files",
        filetypes=[
            ("Image Files", "*.png *.jpg *.jpeg *.webp *.bmp *.tiff"),
            ("All Files", "*.*")
        ]
    )

    if files:
        selected_files = list(files)
    else:
        selected_files = []

    update_selected_label()
    refresh_file_table()
    update_preview()
    clear_results()


def on_drop(event):
    global selected_files
    dropped_files = parse_dropped_files(event.data)

    if not dropped_files:
        messagebox.showwarning("Invalid Files", "No supported image files were dropped.")
        return

    selected_files = dropped_files
    update_selected_label()
    refresh_file_table()
    update_preview()
    clear_results()


def clear_files():
    global selected_files
    selected_files = []
    update_selected_label()
    refresh_file_table()
    update_preview()
    clear_results()


# -----------------------------
# Button hover
# -----------------------------
def on_enter_action(button):
    if button == optimize_button:
        button.config(bg=theme["ACCENT_HOVER"])
    else:
        button.config(bg=theme["BORDER"])


def on_leave_action(button):
    if button == optimize_button:
        button.config(bg=theme["ACCENT"])
    else:
        button.config(bg=theme["CARD_2"])


# -----------------------------
# Main processing
# -----------------------------
def optimize_images():
    if not selected_files:
        messagebox.showwarning("No Images", "Please select or drag image files first.")
        return

    try:
        width = int(width_entry.get())
        height = int(height_entry.get())
        if width <= 0 or height <= 0:
            raise ValueError
    except ValueError:
        messagebox.showerror("Invalid Size", "Please enter valid positive numbers for width and height.")
        return

    base_name = sanitize_filename(rename_entry.get())
    if not base_name:
        messagebox.showerror("Invalid File Name", "Please enter a valid base file name.")
        return

    try:
        start_number = int(start_number_entry.get())
        if start_number < 1:
            raise ValueError
    except ValueError:
        messagebox.showerror("Invalid Starting Number", "Please enter a valid starting number.")
        return

    output_format = format_var.get().upper()
    output_folder = filedialog.askdirectory(title="Select output folder")
    if not output_folder:
        return

    total_before = 0
    total_after = 0
    success_count = 0
    failed_files = []
    total_files = len(selected_files)

    progress_bar["maximum"] = total_files
    progress_bar["value"] = 0
    progress_text_label.config(text=f"0 / {total_files} completed")
    status_label.config(text="Processing images...")
    root.update_idletasks()

    for processed_count, (index, file_path) in enumerate(
        zip(range(start_number, start_number + total_files), selected_files),
        start=1
    ):
        try:
            original_size = os.path.getsize(file_path)
            total_before += original_size

            with Image.open(file_path) as img:
                resized_img = img.copy()
                resized_img.thumbnail((width, height), Image.LANCZOS)

                numbered_name = f"{base_name}-{index}"

                if output_format == "JPG":
                    if resized_img.mode in ("RGBA", "LA", "P"):
                        resized_img = resized_img.convert("RGB")
                    output_path = os.path.join(output_folder, f"{numbered_name}.jpg")
                    resized_img.save(output_path, "JPEG", quality=95, optimize=True)

                elif output_format == "PNG":
                    output_path = os.path.join(output_folder, f"{numbered_name}.png")
                    resized_img.save(output_path, "PNG", optimize=True)

                elif output_format == "WEBP":
                    output_path = os.path.join(output_folder, f"{numbered_name}.webp")
                    resized_img.save(output_path, "WEBP", quality=90, method=6)

                else:
                    raise ValueError("Unsupported output format selected.")

                new_size = os.path.getsize(output_path)
                total_after += new_size
                success_count += 1

        except Exception as e:
            failed_files.append(f"{os.path.basename(file_path)}: {str(e)}")

        progress_bar["value"] = processed_count
        progress_text_label.config(text=f"{processed_count} / {total_files} completed")
        status_label.config(text=f"Processing... {processed_count} of {total_files}")
        root.update_idletasks()

    saved_bytes = total_before - total_after

    result_before_value.config(text=format_bytes(total_before))
    result_after_value.config(text=format_bytes(total_after))
    result_saved_value.config(text=format_bytes(saved_bytes) if saved_bytes >= 0 else "0 B")
    result_count_value.config(text=str(success_count))

    progress_bar["value"] = total_files
    progress_text_label.config(text=f"{total_files} / {total_files} completed")
    status_label.config(text="Done")
    root.update_idletasks()

    if failed_files:
        error_text = "\n".join(failed_files[:10])
        messagebox.showwarning(
            "Completed with Some Errors",
            f"Optimized {success_count} image(s).\n\nSome files failed:\n{error_text}"
        )
    else:
        messagebox.showinfo(
            "Success",
            f"Successfully optimized and saved {success_count} image(s)."
        )


# -----------------------------
# App window
# -----------------------------
root = TkinterDnD.Tk()

icon = tk.PhotoImage(file=resource_path("icon.png"))
root.iconphoto(True, icon)

root.title("Part Hive Image Optimizer")
root.geometry("1180x820")
root.minsize(1180, 820)
root.configure(bg=LIGHT_THEME["BG"])

style = ttk.Style()
try:
    style.theme_use("clam")
except tk.TclError:
    pass

# -----------------------------
# Main layout
# -----------------------------
main_container = tk.Frame(root, bg=LIGHT_THEME["BG"], padx=24, pady=24)
main_container.pack(fill="both", expand=True)

header_frame = tk.Frame(main_container, bg=LIGHT_THEME["BG"])
header_frame.pack(fill="x", pady=(0, 18))

header_left = tk.Frame(header_frame, bg=LIGHT_THEME["BG"])
header_left.pack(side="left", fill="x", expand=True)

app_title = tk.Label(
    header_left,
    text="Image Optimizer",
    font=("Segoe UI", 24, "bold"),
    bg=LIGHT_THEME["BG"],
    fg=LIGHT_THEME["TEXT"]
)
app_title.pack(anchor="w")

app_subtitle = tk.Label(
    header_left,
    text="Resize, rename, convert, and optimize images in one clean workflow.",
    font=("Segoe UI", 11),
    bg=LIGHT_THEME["BG"],
    fg=LIGHT_THEME["MUTED"]
)
app_subtitle.pack(anchor="w", pady=(4, 0))

theme_toggle_wrap = tk.Frame(header_frame, bg=LIGHT_THEME["BG"])
theme_toggle_wrap.pack(side="right", anchor="ne")

theme_label = tk.Label(
    theme_toggle_wrap,
    text="Dark mode",
    font=("Segoe UI", 10),
    bg=LIGHT_THEME["BG"],
    fg=LIGHT_THEME["MUTED"]
)
theme_label.pack(side="left", padx=(0, 8))

theme_var = tk.BooleanVar(value=False)

theme_toggle = tk.Checkbutton(
    theme_toggle_wrap,
    variable=theme_var,
    command=apply_theme,
    bg=LIGHT_THEME["BG"],
    activebackground=LIGHT_THEME["BG"],
    selectcolor=LIGHT_THEME["BG"],
    fg=LIGHT_THEME["TEXT"]
)
theme_toggle.pack(side="left")

content_frame = tk.Frame(main_container, bg=LIGHT_THEME["BG"])
content_frame.pack(fill="both", expand=True)

left_panel = tk.Frame(content_frame, bg=LIGHT_THEME["CARD"], bd=0, highlightthickness=1, highlightbackground=LIGHT_THEME["BORDER"])
left_panel.pack(side="left", fill="both", expand=True, padx=(0, 12))

right_panel = tk.Frame(content_frame, bg=LIGHT_THEME["CARD"], bd=0, highlightthickness=1, highlightbackground=LIGHT_THEME["BORDER"])
right_panel.pack(side="right", fill="both", expand=True)

# -----------------------------
# Left panel
# -----------------------------
left_inner = tk.Frame(left_panel, bg=LIGHT_THEME["CARD"], padx=18, pady=18)
left_inner.pack(fill="both", expand=True)

left_title = tk.Label(left_inner, text="Upload & Settings", font=("Segoe UI", 14, "bold"), bg=LIGHT_THEME["CARD"], fg=LIGHT_THEME["TEXT"])
left_title.pack(anchor="w")

left_desc = tk.Label(left_inner, text="Add your files, set the dimensions, rename pattern, and format.", font=("Segoe UI", 10), bg=LIGHT_THEME["CARD"], fg=LIGHT_THEME["MUTED"])
left_desc.pack(anchor="w", pady=(4, 16))

button_row = tk.Frame(left_inner, bg=LIGHT_THEME["CARD"])
button_row.pack(fill="x", pady=(0, 14))

select_button = tk.Button(
    button_row,
    text="Select Images",
    command=select_images,
    relief="flat",
    bd=0,
    padx=16,
    pady=10,
    font=("Segoe UI", 10, "bold"),
    cursor="hand2"
)
select_button.pack(side="left", padx=(0, 10))
select_button.bind("<Enter>", lambda e: on_enter_action(select_button))
select_button.bind("<Leave>", lambda e: on_leave_action(select_button))

clear_button = tk.Button(
    button_row,
    text="Clear Files",
    command=clear_files,
    relief="flat",
    bd=0,
    padx=16,
    pady=10,
    font=("Segoe UI", 10, "bold"),
    cursor="hand2"
)
clear_button.pack(side="left")
clear_button.bind("<Enter>", lambda e: on_enter_action(clear_button))
clear_button.bind("<Leave>", lambda e: on_leave_action(clear_button))

drop_frame = tk.Frame(left_inner, height=120, highlightthickness=1)
drop_frame.pack(fill="x", pady=(0, 14))
drop_frame.pack_propagate(False)

drop_title = tk.Label(
    drop_frame,
    text="Drag and drop image files here",
    font=("Segoe UI", 12, "bold")
)
drop_title.place(relx=0.5, rely=0.42, anchor="center")

drop_subtitle = tk.Label(
    drop_frame,
    text="Supports PNG, JPG, JPEG, WEBP, BMP, and TIFF",
    font=("Segoe UI", 9)
)
drop_subtitle.place(relx=0.5, rely=0.62, anchor="center")

drop_frame.drop_target_register(DND_FILES)
drop_frame.dnd_bind("<<Drop>>", on_drop)

selected_count_label = tk.Label(left_inner, text="No images selected", font=("Segoe UI", 10, "bold"), bg=LIGHT_THEME["CARD"], fg=LIGHT_THEME["TEXT"])
selected_count_label.pack(anchor="w", pady=(0, 14))

settings_card = tk.Frame(left_inner, highlightthickness=1, padx=14, pady=14)
settings_card.pack(fill="x")

settings_card.grid_columnconfigure(1, weight=1)
settings_card.grid_columnconfigure(3, weight=1)

settings_labels = []

lbl = tk.Label(settings_card, text="Width (px)", font=("Segoe UI", 10))
lbl.grid(row=0, column=0, sticky="w", padx=(0, 10), pady=8)
settings_labels.append(lbl)

width_entry = tk.Entry(settings_card, font=("Segoe UI", 10))
width_entry.grid(row=0, column=1, sticky="ew", pady=8)
width_entry.insert(0, "800")

lbl = tk.Label(settings_card, text="Height (px)", font=("Segoe UI", 10))
lbl.grid(row=0, column=2, sticky="w", padx=(18, 10), pady=8)
settings_labels.append(lbl)

height_entry = tk.Entry(settings_card, font=("Segoe UI", 10))
height_entry.grid(row=0, column=3, sticky="ew", pady=8)
height_entry.insert(0, "600")

lbl = tk.Label(settings_card, text="Base File Name", font=("Segoe UI", 10))
lbl.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=8)
settings_labels.append(lbl)

rename_entry = tk.Entry(settings_card, font=("Segoe UI", 10))
rename_entry.grid(row=1, column=1, sticky="ew", pady=8)
rename_entry.insert(0, "Test")

lbl = tk.Label(settings_card, text="Starting Number", font=("Segoe UI", 10))
lbl.grid(row=1, column=2, sticky="w", padx=(18, 10), pady=8)
settings_labels.append(lbl)

start_number_entry = tk.Entry(settings_card, font=("Segoe UI", 10))
start_number_entry.grid(row=1, column=3, sticky="ew", pady=8)
start_number_entry.insert(0, "1")

lbl = tk.Label(settings_card, text="Output Format", font=("Segoe UI", 10))
lbl.grid(row=2, column=0, sticky="w", padx=(0, 10), pady=(8, 0))
settings_labels.append(lbl)

format_var = tk.StringVar(value="WEBP")
format_menu = ttk.Combobox(
    settings_card,
    textvariable=format_var,
    values=["JPG", "PNG", "WEBP"],
    state="readonly",
    style="Modern.TCombobox",
    font=("Segoe UI", 10)
)
format_menu.grid(row=2, column=1, sticky="ew", pady=(8, 0))
format_menu.current(2)

files_section = tk.Frame(left_inner, bg=LIGHT_THEME["CARD"])
files_section.pack(fill="both", expand=True, pady=(16, 0))

files_title = tk.Label(files_section, text="Loaded Files", font=("Segoe UI", 12, "bold"), bg=LIGHT_THEME["CARD"], fg=LIGHT_THEME["TEXT"])
files_title.pack(anchor="w", pady=(0, 8))

file_list_frame = tk.Frame(files_section, bg=LIGHT_THEME["CARD"])
file_list_frame.pack(fill="both", expand=True)

file_scrollbar = tk.Scrollbar(file_list_frame)
file_scrollbar.pack(side="right", fill="y")

file_listbox = tk.Listbox(
    file_list_frame,
    height=14,
    yscrollcommand=file_scrollbar.set,
    font=("Consolas", 10)
)
file_listbox.pack(fill="both", expand=True)
file_scrollbar.config(command=file_listbox.yview)

# -----------------------------
# Right panel
# -----------------------------
right_inner = tk.Frame(right_panel, bg=LIGHT_THEME["CARD"], padx=18, pady=18)
right_inner.pack(fill="both", expand=True)

right_title = tk.Label(right_inner, text="Preview & Results", font=("Segoe UI", 14, "bold"), bg=LIGHT_THEME["CARD"], fg=LIGHT_THEME["TEXT"])
right_title.pack(anchor="w")

right_desc = tk.Label(right_inner, text="Review the final file names and track the optimization progress.", font=("Segoe UI", 10), bg=LIGHT_THEME["CARD"], fg=LIGHT_THEME["MUTED"])
right_desc.pack(anchor="w", pady=(4, 16))

preview_section = tk.Frame(right_inner, bg=LIGHT_THEME["CARD"])
preview_section.pack(fill="both", expand=True)

preview_title = tk.Label(preview_section, text="Final File Name Preview", font=("Segoe UI", 12, "bold"), bg=LIGHT_THEME["CARD"], fg=LIGHT_THEME["TEXT"])
preview_title.pack(anchor="w", pady=(0, 8))

preview_frame = tk.Frame(preview_section, bg=LIGHT_THEME["CARD"])
preview_frame.pack(fill="both", expand=True, pady=(0, 16))

preview_scrollbar = tk.Scrollbar(preview_frame)
preview_scrollbar.pack(side="right", fill="y")

preview_listbox = tk.Listbox(
    preview_frame,
    height=12,
    yscrollcommand=preview_scrollbar.set,
    font=("Consolas", 10)
)
preview_listbox.pack(fill="both", expand=True)
preview_scrollbar.config(command=preview_listbox.yview)

results_box = tk.Frame(right_inner, highlightthickness=1, padx=14, pady=14)
results_box.pack(fill="x", pady=(0, 16))

result_key_labels = []
result_value_labels = []

lbl = tk.Label(results_box, text="Total Before", font=("Segoe UI", 10))
lbl.grid(row=0, column=0, sticky="w", pady=6)
result_key_labels.append(lbl)

result_before_value = tk.Label(results_box, text="-", font=("Segoe UI", 10, "bold"))
result_before_value.grid(row=0, column=1, sticky="w", padx=(14, 0), pady=6)
result_value_labels.append(result_before_value)

lbl = tk.Label(results_box, text="Total After", font=("Segoe UI", 10))
lbl.grid(row=1, column=0, sticky="w", pady=6)
result_key_labels.append(lbl)

result_after_value = tk.Label(results_box, text="-", font=("Segoe UI", 10, "bold"))
result_after_value.grid(row=1, column=1, sticky="w", padx=(14, 0), pady=6)
result_value_labels.append(result_after_value)

lbl = tk.Label(results_box, text="Saved", font=("Segoe UI", 10))
lbl.grid(row=2, column=0, sticky="w", pady=6)
result_key_labels.append(lbl)

result_saved_value = tk.Label(results_box, text="-", font=("Segoe UI", 10, "bold"))
result_saved_value.grid(row=2, column=1, sticky="w", padx=(14, 0), pady=6)
result_value_labels.append(result_saved_value)

lbl = tk.Label(results_box, text="Processed", font=("Segoe UI", 10))
lbl.grid(row=3, column=0, sticky="w", pady=6)
result_key_labels.append(lbl)

result_count_value = tk.Label(results_box, text="-", font=("Segoe UI", 10, "bold"))
result_count_value.grid(row=3, column=1, sticky="w", padx=(14, 0), pady=6)
result_value_labels.append(result_count_value)

action_row = tk.Frame(right_inner, bg=LIGHT_THEME["CARD"])
action_row.pack(fill="x")

optimize_button = tk.Button(
    action_row,
    text="Optimize, Rename and Save Images",
    command=optimize_images,
    relief="flat",
    bd=0,
    padx=18,
    pady=12,
    font=("Segoe UI", 11, "bold"),
    cursor="hand2"
)
optimize_button.pack(fill="x")
optimize_button.bind("<Enter>", lambda e: on_enter_action(optimize_button))
optimize_button.bind("<Leave>", lambda e: on_leave_action(optimize_button))

progress_wrap = tk.Frame(right_inner, bg=LIGHT_THEME["CARD"])
progress_wrap.pack(fill="x", pady=(16, 0))

progress_bar = ttk.Progressbar(
    progress_wrap,
    orient="horizontal",
    mode="determinate",
    style="Modern.Horizontal.TProgressbar"
)
progress_bar.pack(fill="x")

progress_text_label = tk.Label(
    right_inner,
    text="0 / 0 completed",
    font=("Segoe UI", 10),
    bg=LIGHT_THEME["CARD"],
    fg=LIGHT_THEME["MUTED"]
)
progress_text_label.pack(anchor="w", pady=(8, 0))

status_label = tk.Label(
    right_inner,
    text="Ready",
    font=("Segoe UI", 10, "bold"),
    bg=LIGHT_THEME["CARD"],
    fg=LIGHT_THEME["TEXT"]
)
status_label.pack(anchor="w", pady=(6, 0))

# -----------------------------
# Bindings
# -----------------------------
rename_entry.bind("<KeyRelease>", update_preview)
start_number_entry.bind("<KeyRelease>", update_preview)
format_var.trace_add("write", update_preview)

# -----------------------------
# Start
# -----------------------------
apply_theme()
update_selected_label()
refresh_file_table()
update_preview()
clear_results()

root.mainloop()