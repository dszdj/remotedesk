import argparse
import asyncio
import base64
import io
import json
import os
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk
import pyte
import websockets

ACTIVE_WINDOWS = {}

class TerminalEmulator:
    def __init__(self, cols=120, rows=40):
        self.cols = cols
        self.rows = rows
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.Stream(self.screen)

    def feed(self, text: str):
        self.stream.feed(text)

    def render(self) -> str:
        lines = [line.rstrip() for line in self.screen.display]
        return "\n".join(lines)


class RemoteDeskController:
    def __init__(self, root, host=None, port=None):
        self.root = root
        self.root.title("RemoteDesk - Painel Controlador")
        self.root.geometry("850x540")

        self.ws = None
        self.loop = None
        self.connected = False
        self.admin_id = f"admin_{int(time.time() * 1000)}"
        self.file_buffers = {}

        # Estruturas de histórico para o gerenciador de arquivos
        self.file_history_back = {}
        self.file_history_forward = {}
        self.file_current_path = {}

        self._apply_ultra_dark_theme()

        if host and port:
            self._setup_ui()
            self.connect_to_server(host, port)
        else:
            self._show_connection_dialog()

    def _apply_ultra_dark_theme(self):
        self.bg_color = "#121212"
        self.card_bg = "#1e1e1e"
        self.entry_bg = "#252526"
        self.fg_color = "#e1e1e1"
        self.accent_color = "#007acc"

        self.root.configure(bg=self.bg_color)

        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=self.bg_color, foreground=self.fg_color, font=("Segoe UI", 9))
        style.configure("TLabelframe", background=self.card_bg, foreground=self.accent_color, borderwidth=0)
        style.configure("TLabelframe.Label", background=self.card_bg, foreground=self.accent_color, font=("Segoe UI", 10, "bold"))
        style.configure("TFrame", background=self.bg_color)
        style.configure("TLabel", background=self.card_bg, foreground=self.fg_color)
        style.configure("TButton", background="#2d2d2d", foreground=self.fg_color, borderwidth=0, focuscolor="none")
        style.map("TButton", background=[("active", self.accent_color), ("disabled", "#1a1a1a")], foreground=[("disabled", "#555555")])
        style.configure("TEntry", fieldbackground=self.entry_bg, foreground=self.fg_color, borderwidth=0)
        style.configure("Treeview", background=self.entry_bg, foreground=self.fg_color, fieldbackground=self.entry_bg, borderwidth=0, rowheight=26)
        style.configure("Treeview.Heading", background="#2d2d2d", foreground=self.accent_color, font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", self.accent_color)], foreground=[("selected", "#ffffff")])

    def _show_connection_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Conectar ao Servidor")
        dialog.geometry("320x180")
        dialog.configure(bg=self.bg_color)
        dialog.resizable(False, False)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Endereço IP:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        ent_ip = ttk.Entry(frame, width=15)
        ent_ip.insert(0, "127.0.0.1")
        ent_ip.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(frame, text="Porta:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        ent_port = ttk.Entry(frame, width=15)
        ent_port.insert(0, "8000")
        ent_port.grid(row=1, column=1, padx=5, pady=5)

        def on_confirm():
            ip = ent_ip.get().strip()
            port = ent_port.get().strip()
            if not ip or not port.isdigit():
                messagebox.showerror("Erro", "IP ou Porta inválidos.", parent=dialog)
                return
            dialog.destroy()
            self._setup_ui()
            self.connect_to_server(ip, port)

        btn = ttk.Button(frame, text="Conectar", command=on_confirm)
        btn.grid(row=2, column=0, columnspan=2, pady=15)

    def _setup_ui(self):
        list_frame = ttk.LabelFrame(self.root, text=" Dispositivos Remotos Conectados ", padding=10)
        list_frame.pack(fill="both", expand=True, padx=10, pady=10)

        columns = ("client_id", "hostname", "user", "platform")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        self.tree.heading("client_id", text="ID do Cliente")
        self.tree.heading("hostname", text="Hostname")
        self.tree.heading("user", text="Usuário")
        self.tree.heading("platform", text="Plataforma")

        self.tree.column("client_id", width=220)
        self.tree.column("hostname", width=160)
        self.tree.column("user", width=140)
        self.tree.column("platform", width=120)
        self.tree.pack(fill="both", expand=True)

        self.tree.bind("<Button-3>", self.show_context_menu)

        self.context_menu = tk.Menu(self.root, tearoff=0, bg=self.card_bg, fg=self.fg_color, activebackground=self.accent_color, activeforeground="#ffffff", bd=0)
        self.context_menu.add_command(label="Ver / Controlar Tela", command=self.open_screen_view)
        self.context_menu.add_command(label="Ver Câmera", command=self.open_camera_view)
        self.context_menu.add_command(label="Prompt de Comandos", command=self.open_terminal_view)
        self.context_menu.add_command(label="Gerenciador de Arquivos", command=self.open_file_manager)

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def get_selected_client_id(self):
        selected = self.tree.selection()
        if not selected: return None
        values = self.tree.item(selected[0], "values")
        return values[0] if values else None

    # --- Loop WebSocket Asyncio ---

    def connect_to_server(self, ip, port):
        threading.Thread(target=self.run_async_client, args=(ip, port), daemon=True).start()

    def run_async_client(self, ip, port):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        async def listen():
            uri = f"ws://{ip}:{port}"
            try:
                async with websockets.connect(uri, max_size=None) as ws:
                    self.ws = ws
                    self.connected = True

                    reg_payload = {"type": "register", "role": "admin", "client_id": self.admin_id}
                    await ws.send(json.dumps(reg_payload))

                    async for message in ws:
                        data = json.loads(message)
                        msg_type = data.get("type")

                        if msg_type == "client_list_update":
                            self.root.after(0, self.update_client_list, data.get("clients", []))
                        elif msg_type == "screen":
                            c_id = data.get("client_id")
                            self.root.after(0, self.update_frame, f"screen_{c_id}", data.get("frame"), data.get("width"), data.get("height"))
                        elif msg_type == "camera":
                            c_id = data.get("client_id")
                            self.root.after(0, self.update_frame, f"camera_{c_id}", data.get("frame"))
                        elif msg_type == "terminal_output":
                            c_id = data.get("client_id")
                            self.root.after(0, self.append_terminal_text, c_id, data.get("data"))
                        elif msg_type == "file_list_result":
                            c_id = data.get("client_id")
                            self.root.after(0, self.populate_files, c_id, data.get("path"), data.get("entries", []))
                        elif msg_type == "file_transfer_chunk":
                            self.process_file_chunk(data.get("transfer_id"), data.get("data"))
                        elif msg_type == "file_transfer_end":
                            self.root.after(0, self.finalize_file_download, data.get("transfer_id"))

            except Exception as e:
                messagebox.showerror("Erro de Conexão", f"Falha ao conectar no servidor ws://{ip}:{port}\n{e}")

        self.loop.run_until_complete(listen())

    def send_cmd(self, target_id, payload):
        if self.ws and self.connected and self.loop:
            payload["target_id"] = target_id
            asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(payload)), self.loop)

    def update_client_list(self, clients):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for c in clients:
            self.tree.insert("", "end", values=(c["client_id"], c["hostname"], c["user"], c["platform"]))

    # --- Funções de Controle Remoto ---

    def open_screen_view(self):
        target_id = self.get_selected_client_id()
        if not target_id: return

        win_key = f"screen_{target_id}"
        if win_key in ACTIVE_WINDOWS and ACTIVE_WINDOWS[win_key].winfo_exists():
            ACTIVE_WINDOWS[win_key].lift()
            return

        win = tk.Toplevel(self.root)
        win.title(f"Tela Remota - {target_id}")
        win.geometry("960x600")
        win.configure(bg=self.bg_color)

        bar = tk.Frame(win, bg=self.card_bg, height=35)
        bar.pack(fill="x", side="top")

        is_controlling = tk.BooleanVar(value=False)
        btn_control = ttk.Button(bar, text="⚪ Controlar (Desativado)", command=lambda: [
            is_controlling.set(not is_controlling.get()),
            btn_control.config(text="🟢 Controlando (Ativo)" if is_controlling.get() else "⚪ Controlar (Desativado)")
        ])
        btn_control.pack(side="left", padx=10, pady=5)

        lbl_img = tk.Label(win, bg="black", bd=0)
        lbl_img.pack(fill="both", expand=True)

        ACTIVE_WINDOWS[win_key] = win
        ACTIVE_WINDOWS[f"screen_dims_{target_id}"] = {"remote_w": 1920, "remote_h": 1080}

        state = {"last_motion_time": 0}

        def send_mouse(event, action, button="left"):
            if not is_controlling.get(): return
            
            if action == "move":
                current_time = time.time()
                if current_time - state["last_motion_time"] < 0.05:
                    return
                state["last_motion_time"] = current_time

            dims = ACTIVE_WINDOWS.get(f"screen_dims_{target_id}", {})
            disp_w = lbl_img.winfo_width()
            disp_h = lbl_img.winfo_height()
            if disp_w <= 0 or disp_h <= 0: return

            click_x = max(0, min(event.x, disp_w))
            click_y = max(0, min(event.y, disp_h))

            target_x = (click_x / disp_w) * dims.get("remote_w", 1920)
            target_y = (click_y / disp_h) * dims.get("remote_h", 1080)

            self.send_cmd(target_id, {
                "type": "command", 
                "command_type": "mouse", 
                "action": action, 
                "x": target_x, 
                "y": target_y, 
                "button": button
            })

        lbl_img.bind("<Motion>", lambda e: send_mouse(e, "move"))
        lbl_img.bind("<Button-1>", lambda e: [lbl_img.focus_set(), send_mouse(e, "click", "left")])
        lbl_img.bind("<Button-3>", lambda e: send_mouse(e, "right_click", "right"))
        lbl_img.bind("<Double-Button-1>", lambda e: send_mouse(e, "double_click", "left"))
        lbl_img.bind("<B1-Motion>", lambda e: send_mouse(e, "drag", "left"))
        
        lbl_img.bind("<MouseWheel>", lambda e: is_controlling.get() and self.send_cmd(target_id, {
            "type": "command", 
            "command_type": "scroll", 
            "amount": int(e.delta / 10)
        }))

        def on_key(event):
            if not is_controlling.get(): return
            char = event.char
            
            if event.state & 0x4 and char:
                self.send_cmd(target_id, {
                    "type": "command", 
                    "command_type": "shortcut", 
                    "keys": ["ctrl", char.lower()]
                })
            elif char and len(char) == 1 and ord(char) >= 32:
                self.send_cmd(target_id, {
                    "type": "command", 
                    "command_type": "keyboard", 
                    "text": char
                })
            else:
                self.send_cmd(target_id, {
                    "type": "command", 
                    "command_type": "keyboard", 
                    "key": event.keysym
                })

        win.bind("<Key>", on_key)
        self.send_cmd(target_id, {"type": "command", "command_type": "screen_open", "use_ffmpeg": False})

        def on_close():
            self.send_cmd(target_id, {"type": "command", "command_type": "screen_close"})
            ACTIVE_WINDOWS.pop(f"screen_dims_{target_id}", None)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

    def open_terminal_view(self):
        target_id = self.get_selected_client_id()
        if not target_id: return

        win_key = f"terminal_{target_id}"
        if win_key in ACTIVE_WINDOWS and ACTIVE_WINDOWS[win_key].winfo_exists():
            ACTIVE_WINDOWS[win_key].lift()
            return

        win = tk.Toplevel(self.root)
        win.title(f"Prompt de Comandos - {target_id}")
        win.geometry("800x450")
        win.configure(bg="#000000")

        txt_terminal = tk.Text(
            win, bg="#000000", fg="#00ff00", insertbackground="#00ff00", insertwidth=3,
            font=("Consolas", 11), bd=0, highlightthickness=0, wrap="none"
        )
        txt_terminal.pack(fill="both", expand=True, padx=8, pady=8)
        txt_terminal.focus_set()

        emulator = TerminalEmulator(cols=120, rows=40)
        ACTIVE_WINDOWS[win_key] = win
        ACTIVE_WINDOWS[f"term_emu_{target_id}"] = emulator

        key_map = {
            "Return": "\r", "BackSpace": "\x08", "Tab": "\t", "Escape": "\x1b",
            "Up": "\x1b[A", "Down": "\x1b[B", "Right": "\x1b[C", "Left": "\x1b[D",
            "Home": "\x1b[H", "End": "\x1b[F", "Delete": "\x1b[3~",
        }

        def on_key_press(event):
            if event.state & 0x4:
                k = event.keysym.lower()
                payload_data = "\x03" if k == "c" else ("\x1a" if k == "z" else "")
            elif event.keysym in key_map:
                payload_data = key_map[event.keysym]
            elif event.char:
                payload_data = event.char
            else:
                return "break"

            if payload_data:
                self.send_cmd(target_id, {"type": "command", "command_type": "terminal_input", "data": payload_data})
            return "break"

        txt_terminal.bind("<Key>", on_key_press)
        self.send_cmd(target_id, {"type": "command", "command_type": "terminal_open"})

        def on_close():
            self.send_cmd(target_id, {"type": "command", "command_type": "terminal_close"})
            ACTIVE_WINDOWS.pop(f"term_emu_{target_id}", None)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

    def append_terminal_text(self, target_id, text):
        win_key = f"terminal_{target_id}"
        emu_key = f"term_emu_{target_id}"
        if win_key in ACTIVE_WINDOWS and ACTIVE_WINDOWS[win_key].winfo_exists():
            emulator = ACTIVE_WINDOWS.get(emu_key)
            win = ACTIVE_WINDOWS[win_key]
            
            txt_widgets = [c for c in win.winfo_children() if isinstance(c, tk.Text)]
            if not txt_widgets: return
            txt = txt_widgets[0]

            if emulator:
                emulator.feed(text)
                txt.delete("1.0", "end")
                txt.insert("1.0", emulator.render())
                
                cursor_pos = f"{emulator.screen.cursor.y + 1}.{emulator.screen.cursor.x}"
                txt.mark_set("insert", cursor_pos)
                txt.see("insert")

    def open_camera_view(self):
        target_id = self.get_selected_client_id()
        if not target_id: return

        win_key = f"camera_{target_id}"
        if win_key in ACTIVE_WINDOWS and ACTIVE_WINDOWS[win_key].winfo_exists(): return

        win = tk.Toplevel(self.root)
        win.title(f"Câmera Remota - {target_id}")
        win.geometry("640x480")
        win.configure(bg=self.bg_color)

        lbl_img = tk.Label(win, bg="black", bd=0)
        lbl_img.pack(fill="both", expand=True)
        ACTIVE_WINDOWS[win_key] = win

        self.send_cmd(target_id, {"type": "command", "command_type": "camera_open"})
        win.protocol("WM_DELETE_WINDOW", lambda: [self.send_cmd(target_id, {"type": "command", "command_type": "camera_close"}), win.destroy()])

    # --- Gerenciador de Arquivos com Navegação ---

    def file_navigate(self, target_id, new_path, record_history=True):
        current = self.file_current_path.get(target_id, "")
        if record_history and current and current != new_path:
            if target_id not in self.file_history_back:
                self.file_history_back[target_id] = []
            self.file_history_back[target_id].append(current)
            self.file_history_forward[target_id] = []

        self.send_cmd(target_id, {"type": "command", "command_type": "file_list", "path": new_path})

    def file_go_back(self, target_id):
        history = self.file_history_back.get(target_id, [])
        if history:
            prev_path = history.pop()
            current = self.file_current_path.get(target_id, "")
            if current:
                if target_id not in self.file_history_forward:
                    self.file_history_forward[target_id] = []
                self.file_history_forward[target_id].append(current)
            self.file_navigate(target_id, prev_path, record_history=False)

    def file_go_forward(self, target_id):
        history = self.file_history_forward.get(target_id, [])
        if history:
            next_path = history.pop()
            current = self.file_current_path.get(target_id, "")
            if current:
                if target_id not in self.file_history_back:
                    self.file_history_back[target_id] = []
                self.file_history_back[target_id].append(current)
            self.file_navigate(target_id, next_path, record_history=False)

    def open_file_manager(self):
        target_id = self.get_selected_client_id()
        if not target_id: return

        win_key = f"files_{target_id}"
        if win_key in ACTIVE_WINDOWS and ACTIVE_WINDOWS[win_key].winfo_exists(): return

        win = tk.Toplevel(self.root)
        win.title(f"Gerenciador de Arquivos - {target_id}")
        win.geometry("850x520")
        win.configure(bg=self.bg_color)

        top_frame = ttk.Frame(win)
        top_frame.pack(fill="x", padx=10, pady=5)

        btn_back = ttk.Button(top_frame, text="◀ Voltar", width=9, command=lambda: self.file_go_back(target_id))
        btn_back.pack(side="left", padx=2)

        btn_forward = ttk.Button(top_frame, text="▶ Avançar", width=9, command=lambda: self.file_go_forward(target_id))
        btn_forward.pack(side="left", padx=2)

        ent_path = ttk.Entry(top_frame)
        ent_path.pack(side="left", fill="x", expand=True, padx=5)

        btn_go = ttk.Button(top_frame, text="Ir", width=5, command=lambda: self.file_navigate(target_id, ent_path.get()))
        btn_go.pack(side="left", padx=2)

        action_frame = ttk.Frame(win)
        action_frame.pack(fill="x", padx=10, pady=2)

        btn_upload = ttk.Button(action_frame, text="Subir Arquivo (Upload)", command=lambda: self.upload_file(target_id, ent_path.get()))
        btn_upload.pack(side="left", padx=5)

        btn_download = ttk.Button(action_frame, text="Baixar Arquivo", command=lambda: self.download_selected_file(target_id, tree_files, ent_path.get(), "download"))
        btn_download.pack(side="left", padx=5)

        btn_view = ttk.Button(action_frame, text="Visualizar / Reproduzir", command=lambda: self.download_selected_file(target_id, tree_files, ent_path.get(), "view"))
        btn_view.pack(side="left", padx=5)

        tree_files = ttk.Treeview(win, columns=("name", "size", "type"), show="headings")
        tree_files.heading("name", text="Nome")
        tree_files.heading("size", text="Tamanho (Bytes)")
        tree_files.heading("type", text="Tipo")
        tree_files.column("name", width=450)
        tree_files.column("size", width=130)
        tree_files.column("type", width=110)
        tree_files.pack(fill="both", expand=True, padx=10, pady=5)

        def on_double_click(event):
            item = tree_files.selection()
            if not item: return
            values = tree_files.item(item[0], "values")
            if not values: return
            name = values[0]
            f_type = values[2]
            current_dir = ent_path.get()

            if name == "..":
                parent_dir = os.path.dirname(current_dir)
                if parent_dir and parent_dir != current_dir:
                    self.file_navigate(target_id, parent_dir)
            elif f_type == "Folder":
                new_dir = os.path.join(current_dir, name)
                self.file_navigate(target_id, new_dir)
            else:
                ext = os.path.splitext(name)[1].lower()
                if ext in [".png", ".jpg", ".jpeg", ".bmp", ".gif"]:
                    self.download_selected_file(target_id, tree_files, current_dir, mode="view")
                else:
                    self.download_selected_file(target_id, tree_files, current_dir, mode="view")

        tree_files.bind("<Double-1>", on_double_click)

        ACTIVE_WINDOWS[win_key] = win
        ACTIVE_WINDOWS[f"files_tree_{target_id}"] = tree_files
        ACTIVE_WINDOWS[f"files_path_{target_id}"] = ent_path

        self.file_navigate(target_id, "", record_history=False)

    def update_frame(self, win_key, base64_data, width=None, height=None):
        if win_key in ACTIVE_WINDOWS and ACTIVE_WINDOWS[win_key].winfo_exists():
            try:
                raw_bytes = base64.b64decode(base64_data)
                img = Image.open(io.BytesIO(raw_bytes))

                target_id = win_key.replace("screen_", "").replace("camera_", "")
                if width and height:
                    ACTIVE_WINDOWS[f"screen_dims_{target_id}"] = {"remote_w": width, "remote_h": height}

                win = ACTIVE_WINDOWS[win_key]
                lbl_widgets = [c for c in win.winfo_children() if isinstance(c, tk.Label)]
                if not lbl_widgets: return
                lbl = lbl_widgets[0]

                w, h = lbl.winfo_width(), lbl.winfo_height()
                if w > 10 and h > 10:
                    img = img.resize((w, h), Image.Resampling.LANCZOS)

                tk_img = ImageTk.PhotoImage(img)
                lbl.configure(image=tk_img)
                lbl.image = tk_img
            except Exception:
                pass

    def populate_files(self, target_id, path, entries):
        if f"files_tree_{target_id}" in ACTIVE_WINDOWS:
            tree = ACTIVE_WINDOWS[f"files_tree_{target_id}"]
            ent_path = ACTIVE_WINDOWS[f"files_path_{target_id}"]

            self.file_current_path[target_id] = path
            ent_path.delete(0, "end")
            ent_path.insert(0, path)

            for item in tree.get_children():
                tree.delete(item)

            parent = os.path.dirname(path)
            if parent and parent != path:
                tree.insert("", "end", values=("..", "-", "Folder"))

            for entry in entries:
                is_dir = entry.get("is_dir")
                f_type = "Folder" if is_dir else "File"
                tree.insert("", "end", values=(entry.get("name"), entry.get("size") if not is_dir else "-", f_type))

    def download_selected_file(self, target_id, tree, current_dir, mode="download"):
        item = tree.selection()
        if not item: return
        values = tree.item(item[0], "values")
        if not values: return
        file_name = values[0]
        file_type = values[2]
        if file_type == "Folder" or file_name == "..": return

        remote_path = os.path.join(current_dir, file_name)
        transfer_id = f"tr_{int(time.time() * 1000)}"

        save_path = filedialog.asksaveasfilename(initialfile=file_name) if mode == "download" else tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_name)[1]).name

        self.file_buffers[transfer_id] = {"path": save_path, "mode": mode, "filename": file_name, "data": io.BytesIO()}
        self.send_cmd(target_id, {"type": "command", "command_type": "file_download", "path": remote_path, "transfer_id": transfer_id})

    def upload_file(self, target_id, remote_dir):
        local_path = filedialog.askopenfilename()
        if not local_path: return
        file_name = os.path.basename(local_path)
        remote_path = os.path.join(remote_dir, file_name)
        transfer_id = f"up_{int(time.time() * 1000)}"

        def run_upload():
            try:
                size = os.path.getsize(local_path)
                self.send_cmd(target_id, {"type": "command", "command_type": "file_transfer_start", "transfer_id": transfer_id, "destination": remote_path, "size": size})
                with open(local_path, "rb") as f:
                    while chunk := f.read(64 * 1024):
                        encoded = base64.b64encode(chunk).decode("ascii")
                        self.send_cmd(target_id, {"type": "command", "command_type": "file_transfer_chunk", "transfer_id": transfer_id, "data": encoded})
                self.send_cmd(target_id, {"type": "command", "command_type": "file_transfer_end", "transfer_id": transfer_id})
                messagebox.showinfo("Sucesso", f"Upload concluído: {file_name}")
            except Exception as e:
                messagebox.showerror("Erro", f"Falha no upload: {e}")

        threading.Thread(target=run_upload, daemon=True).start()

    def process_file_chunk(self, transfer_id, base64_data):
        if transfer_id in self.file_buffers:
            self.file_buffers[transfer_id]["data"].write(base64.b64decode(base64_data))

    def finalize_file_download(self, transfer_id):
        if transfer_id not in self.file_buffers: return
        buf_info = self.file_buffers.pop(transfer_id)
        save_path, mode, filename = buf_info["path"], buf_info["mode"], buf_info["filename"]

        with open(save_path, "wb") as f:
            f.write(buf_info["data"].getvalue())

        if mode == "download":
            messagebox.showinfo("Download", f"Salvo em: {save_path}")
        else:
            if os.path.splitext(filename)[1].lower() in [".png", ".jpg", ".jpeg", ".gif", ".bmp"]:
                win = tk.Toplevel(self.root)
                win.title(f"Visualizador - {filename}")
                win.configure(bg=self.bg_color)
                img = Image.open(save_path)
                img.thumbnail((800, 600))
                tk_img = ImageTk.PhotoImage(img)
                lbl = tk.Label(win, image=tk_img, bg=self.bg_color, bd=0)
                lbl.image = tk_img
                lbl.pack(padx=10, pady=10)
            else:
                os.startfile(save_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=None, help="IP do Servidor")
    parser.add_argument("--port", default=None, help="Porta do Servidor")
    args = parser.parse_args()

    root = tk.Tk()
    app = RemoteDeskController(root, host=args.host, port=args.port)
    root.mainloop()
