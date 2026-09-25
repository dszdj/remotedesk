import argparse
import base64
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import websocket


class ClientWindow:
    def __init__(self, server_url: str, client_id: str, user: str, ip: str, port: str, initial_tab: str):
        self.server_url = server_url
        self.client_id = client_id
        self.messages: queue.Queue[dict] = queue.Queue()
        self.socket = None
        self.downloads: dict[str, dict] = {}

        self.root = tk.Tk()
        self.root.title(f"Cliente {user}@{ip}:{port}")
        self.root.geometry("900x600")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Ver Tela", command=lambda: self.send_tool("screen")).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Ver Camera", command=lambda: self.send_tool("camera")).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Terminal", command=lambda: self.tabs.select(self.terminal_tab)).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Arquivos", command=lambda: self.tabs.select(self.files_tab)).pack(side=tk.LEFT, padx=3)

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.terminal_tab = ttk.Frame(self.tabs)
        self.files_tab = ttk.Frame(self.tabs)
        self.tabs.add(self.terminal_tab, text="Terminal")
        self.tabs.add(self.files_tab, text="Arquivos")

        self.terminal_output = ScrolledText(self.terminal_tab, background="#050505", foreground="#f5f5f5", insertbackground="#f5f5f5", font=("Consolas", 11))
        self.terminal_output.pack(fill=tk.BOTH, expand=True)
        terminal_bar = ttk.Frame(self.terminal_tab, padding=(0, 6, 0, 0))
        terminal_bar.pack(fill=tk.X)
        self.terminal_input = ttk.Entry(terminal_bar)
        self.terminal_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.terminal_input.bind("<Return>", self.send_terminal)
        ttk.Button(terminal_bar, text="Enviar", command=self.send_terminal).pack(side=tk.LEFT, padx=(6, 0))

        file_bar = ttk.Frame(self.files_tab, padding=6)
        file_bar.pack(fill=tk.X)
        self.path_input = ttk.Entry(file_bar)
        self.path_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(file_bar, text="Listar", command=self.list_files).pack(side=tk.LEFT, padx=4)
        ttk.Button(file_bar, text="Baixar", command=self.download_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(file_bar, text="Enviar arquivo", command=self.upload_file).pack(side=tk.LEFT, padx=4)
        self.file_tree = ttk.Treeview(self.files_tab, columns=("type", "size", "path"), show="headings")
        self.file_tree.heading("type", text="Tipo")
        self.file_tree.heading("size", text="Tamanho")
        self.file_tree.heading("path", text="Caminho")
        self.file_tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        self.connect()
        self.root.after(100, self.process_messages)
        if initial_tab == "files":
            self.tabs.select(self.files_tab)
        else:
            self.tabs.select(self.terminal_tab)

    def connect(self):
        def run():
            try:
                self.socket = websocket.create_connection(self.server_url, timeout=10)
                self.socket.send(json.dumps({"type": "ui", "role": "client_window"}))
                while True:
                    self.messages.put(json.loads(self.socket.recv()))
            except Exception as exc:
                self.messages.put({"type": "error", "error": str(exc)})

        threading.Thread(target=run, daemon=True).start()

    def process_messages(self):
        try:
            while True:
                message = self.messages.get_nowait()
                message_type = message.get("type")
                if message_type == "terminal_output":
                    self.terminal_output.insert(tk.END, message.get("data", ""))
                    self.terminal_output.see(tk.END)
                elif message_type == "file_list_result":
                    self.render_files(message)
                elif message_type == "file_transfer_start" and message.get("direction") == "download":
                    self.downloads[message["transfer_id"]] = {"name": message.get("name", "download.bin"), "chunks": []}
                elif message_type == "file_transfer_chunk" and message.get("direction") == "download":
                    transfer = self.downloads.get(message.get("transfer_id"))
                    if transfer:
                        transfer["chunks"].append(message["data"])
                elif message_type == "file_transfer_end" and message.get("direction") == "download":
                    self.finish_download(message["transfer_id"])
                elif message_type == "file_transfer_error":
                    messagebox.showerror("Transferência", message.get("error", "Erro desconhecido"))
        except queue.Empty:
            pass
        self.root.after(100, self.process_messages)

    def send(self, payload: dict):
        if self.socket is not None:
            self.socket.send(json.dumps(payload))

    def send_tool(self, tool: str):
        self.send({"type": f"{tool}_control", "client_id": self.client_id, "action": "open"})

    def send_terminal(self, event=None):
        command = self.terminal_input.get()
        if command:
            self.send({"type": "terminal_input", "client_id": self.client_id, "data": command + "\r"})
            self.terminal_input.delete(0, tk.END)

    def list_files(self):
        self.send({"type": "file_list", "client_id": self.client_id, "path": self.path_input.get()})

    def render_files(self, message):
        self.path_input.delete(0, tk.END)
        self.path_input.insert(0, message.get("path", ""))
        self.file_tree.delete(*self.file_tree.get_children())
        if message.get("error"):
            self.file_tree.insert("", tk.END, values=("ERRO", "", message["error"]))
            return
        for entry in message.get("entries", []):
            kind = "Pasta" if entry.get("is_dir") else "Arquivo"
            item = self.file_tree.insert("", tk.END, values=(kind, entry.get("size", 0), entry.get("path", "")))
            if entry.get("is_dir"):
                self.file_tree.item(item, tags=("folder",))

    def download_selected(self):
        selection = self.file_tree.selection()
        if not selection:
            return
        path = self.file_tree.item(selection[0], "values")[2]
        transfer_id = f"download-{id(selection)}"
        self.send({"type": "file_download", "client_id": self.client_id, "path": path, "transfer_id": transfer_id})

    def upload_file(self):
        source = filedialog.askopenfilename()
        destination = self.path_input.get().strip()
        if not source or not destination:
            return
        if Path(destination).is_dir():
            destination = str(Path(destination) / Path(source).name)
        transfer_id = f"upload-{Path(source).name}-{id(source)}"
        file_size = Path(source).stat().st_size
        self.send({"type": "file_transfer_start", "client_id": self.client_id, "transfer_id": transfer_id, "destination": destination, "size": file_size})
        with open(source, "rb") as file_handle:
            while chunk := file_handle.read(64 * 1024):
                self.send({"type": "file_transfer_chunk", "client_id": self.client_id, "transfer_id": transfer_id, "data": base64.b64encode(chunk).decode("ascii")})
        self.send({"type": "file_transfer_end", "client_id": self.client_id, "transfer_id": transfer_id})

    def finish_download(self, transfer_id: str):
        transfer = self.downloads.pop(transfer_id, None)
        if not transfer:
            return
        target = filedialog.asksaveasfilename(initialfile=transfer["name"])
        if target:
            with open(target, "wb") as file_handle:
                file_handle.write(base64.b64decode("".join(transfer["chunks"])))

    def close(self):
        self.send({"type": "terminal_control", "client_id": self.client_id, "action": "close"})
        self.send({"type": "screen_control", "client_id": self.client_id, "action": "close"})
        self.send({"type": "camera_control", "client_id": self.client_id, "action": "close"})
        if self.socket is not None:
            self.socket.close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ferramentas de um cliente remoto")
    parser.add_argument("--server", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--tab", choices=("terminal", "files"), default="terminal")
    args = parser.parse_args()
    ClientWindow(args.server, args.client_id, args.user, args.ip, args.port, args.tab).run()
