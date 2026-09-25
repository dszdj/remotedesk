import argparse
import json
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import websocket


class ControlCenter:
    def __init__(self, server_url: str | None = None):
        self.server_url = server_url
        self.socket = None
        self.server_process: subprocess.Popen | None = None
        self.messages: queue.Queue[dict] = queue.Queue()
        self.clients: dict[str, dict] = {}
        self.windows: list[subprocess.Popen] = []

        self.root = tk.Tk()
        self.root.title("Remote Desk")
        self.root.geometry("760x430")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        toolbar = ttk.Frame(self.root, padding=10)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="IP do servidor:").pack(side=tk.LEFT)
        self.host_input = ttk.Entry(toolbar, width=16)
        self.host_input.insert(0, "0.0.0.0")
        self.host_input.pack(side=tk.LEFT, padx=(5, 12))
        ttk.Label(toolbar, text="Porta:").pack(side=tk.LEFT)
        self.port_input = ttk.Entry(toolbar, width=7)
        self.port_input.insert(0, "8000")
        self.port_input.pack(side=tk.LEFT, padx=(5, 12))
        self.start_button = ttk.Button(toolbar, text="Start Server", command=self.start_server)
        self.start_button.pack(side=tk.LEFT)
        self.status_label = ttk.Label(toolbar, text="Servidor parado")
        self.status_label.pack(side=tk.LEFT, padx=12)
        ttk.Button(toolbar, text="Atualizar", command=self.refresh).pack(side=tk.RIGHT)

        ttk.Label(self.root, text="Clientes conectados", padding=(10, 0, 10, 6)).pack(anchor=tk.W)

        columns = ("client", "user", "address")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings")
        self.tree.heading("client", text="Cliente")
        self.tree.heading("user", text="Usuário")
        self.tree.heading("address", text="Endereço")
        self.tree.column("client", width=260)
        self.tree.column("user", width=160)
        self.tree.column("address", width=220)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.tree.bind("<Button-3>", self.open_context_menu)
        self.tree.bind("<Double-1>", lambda event: self.open_client_window("terminal"))

        self.menu = tk.Menu(self.root, tearoff=False)
        self.menu.add_command(label="Abrir janela do cliente", command=lambda: self.open_client_window("terminal"))
        self.menu.add_separator()
        self.menu.add_command(label="Ver Tela", command=lambda: self.send_tool("screen"))
        self.menu.add_command(label="Ver Camera", command=lambda: self.send_tool("camera"))
        self.menu.add_command(label="Terminal", command=lambda: self.open_client_window("terminal"))
        self.menu.add_command(label="Explorar Arquivos", command=lambda: self.open_client_window("files"))

        self.root.after(100, self.process_messages)

    def start_server(self):
        if self.server_process is not None and self.server_process.poll() is None:
            return
        try:
            host = self.host_input.get().strip() or "0.0.0.0"
            port = int(self.port_input.get().strip())
        except ValueError:
            self.status_label.config(text="Porta inválida")
            return
        self.server_url = f"ws://127.0.0.1:{port}/ws"
        self.server_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "server:app",
                "--host",
                host,
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=str(Path(__file__).parent),
        )
        self.start_button.config(state=tk.DISABLED)
        self.status_label.config(text=f"Iniciando em {host}:{port}...")
        self.root.after(1000, self.connect)

    def connect(self):
        if not self.server_url or self.socket is not None:
            return

        def run():
            while self.server_process is not None and self.server_process.poll() is None:
                try:
                    self.socket = websocket.create_connection(self.server_url, timeout=10)
                    self.socket.send(json.dumps({"type": "ui", "role": "control"}))
                    self.messages.put({"type": "status", "text": "Servidor conectado"})
                    while True:
                        self.messages.put(json.loads(self.socket.recv()))
                except Exception:
                    self.socket = None
                    self.messages.put({"type": "status", "text": "Aguardando servidor..."})
                    time.sleep(1)

        threading.Thread(target=run, daemon=True).start()

    def process_messages(self):
        try:
            while True:
                message = self.messages.get_nowait()
                if message.get("type") == "clients":
                    self.clients = message.get("details", {})
                    self.refresh()
                elif message.get("type") == "status":
                    self.status_label.config(text=message.get("text", ""))
        except queue.Empty:
            pass
        self.root.after(100, self.process_messages)

    def refresh(self):
        current = self.tree.selection()
        selected = current[0] if current else None
        self.tree.delete(*self.tree.get_children())
        for client_id, info in sorted(self.clients.items()):
            address = f"{info.get('ip', 'unknown')}:{info.get('port', 0)}"
            self.tree.insert("", tk.END, iid=client_id, values=(client_id, info.get("user", "unknown"), address))
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)

    def selected_client(self):
        selection = self.tree.selection()
        if not selection:
            return None
        client_id = selection[0]
        return client_id, self.clients.get(client_id, {})

    def open_context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.menu.tk_popup(event.x_root, event.y_root)

    def send_tool(self, tool: str):
        selected = self.selected_client()
        if not selected or self.socket is None:
            return
        client_id, _ = selected
        message_type = "screen_control" if tool == "screen" else "camera_control"
        self.socket.send(json.dumps({"type": message_type, "client_id": client_id, "action": "open"}))

    def open_client_window(self, initial_tab: str):
        selected = self.selected_client()
        if not selected:
            return
        client_id, info = selected
        viewer = Path(__file__).with_name("client_window.py")
        process = subprocess.Popen([
            sys.executable,
            str(viewer),
            "--server", self.server_url,
            "--client-id", client_id,
            "--user", str(info.get("user", "unknown")),
            "--ip", str(info.get("ip", "unknown")),
            "--port", str(info.get("port", 0)),
            "--tab", initial_tab,
        ], cwd=str(viewer.parent))
        self.windows.append(process)

    def close(self):
        for process in self.windows:
            if process.poll() is None:
                process.terminate()
        if self.socket is not None:
            self.socket.close()
        if self.server_process is not None and self.server_process.poll() is None:
            self.server_process.terminate()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Control Center nativo")
    parser.add_argument("--server", default=None)
    args = parser.parse_args()
    ControlCenter(args.server).run()
