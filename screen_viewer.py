import argparse
import asyncio
import base64
import io
import json
import queue
import threading
import tkinter as tk
from typing import Any

import pyautogui
import websocket
from aiortc import RTCPeerConnection, RTCSessionDescription
from PIL import Image, ImageTk


class ScreenViewer:
    def __init__(self, server_url: str, client_id: str, title: str, mode: str):
        self.server_url = server_url
        self.client_id = client_id
        self.mode = mode
        self.ws: websocket.WebSocketApp | None = None
        self.ws_lock = threading.Lock()
        self.frames: queue.Queue[Any] = queue.Queue()
        self.peer: RTCPeerConnection | None = None
        self.webrtc_loop: asyncio.AbstractEventLoop | None = None
        self.source_width = 1
        self.source_height = 1
        self.image_box = (0, 0, 1, 1)
        self.photo: ImageTk.PhotoImage | None = None

        self.root = tk.Tk()
        self.root.title(title)
        self.root.geometry("1100x700")
        self.root.configure(background="#111111")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.canvas = tk.Canvas(self.root, background="#111111", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self.left_click)
        self.canvas.bind("<Button-3>", self.right_click)
        self.canvas.bind("<MouseWheel>", self.scroll)
        self.canvas.bind("<KeyPress>", self.key_press)
        self.canvas.focus_set()

    def send(self, payload: dict[str, Any]):
        if self.ws is None:
            return
        try:
            with self.ws_lock:
                self.ws.send(json.dumps(payload))
        except Exception:
            self.root.after(0, self.close)

    def on_open(self, ws):
        self.send({"type": "ui", "role": self.mode})
        self.send({"type": "select_client", "client_id": self.client_id})
        if self.mode == "screen":
            self.send({"type": "screen_control", "client_id": self.client_id, "action": "open"})
            threading.Thread(target=self.start_webrtc, daemon=True).start()
        else:
            self.send({"type": "camera_control", "client_id": self.client_id, "action": "open"})

    def on_message(self, ws, message):
        try:
            payload = json.loads(message)
            if payload.get("type") == "webrtc_answer" and self.webrtc_loop:
                asyncio.run_coroutine_threadsafe(
                    self.apply_webrtc_answer(payload["answer"]),
                    self.webrtc_loop,
                )
                return
            if payload.get("type") == self.mode and payload.get("client_id") == self.client_id:
                self.frames.put(payload)
        except Exception:
            pass

    def on_close(self, ws, code, reason):
        self.root.after(0, self.close)

    def on_error(self, ws, error):
        self.root.after(0, self.close)

    def start_webrtc(self):
        self.webrtc_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.webrtc_loop)
        self.webrtc_loop.run_until_complete(self.create_webrtc_offer())
        self.webrtc_loop.run_forever()

    async def create_webrtc_offer(self):
        self.peer = RTCPeerConnection()
        self.peer.addTransceiver("video", direction="recvonly")

        @self.peer.on("track")
        def on_track(track):
            if track.kind == "video":
                asyncio.ensure_future(self.consume_video(track))

        offer = await self.peer.createOffer()
        await self.peer.setLocalDescription(offer)
        self.send(
            {
                "type": "webrtc_offer",
                "client_id": self.client_id,
                "offer": {
                    "sdp": self.peer.localDescription.sdp,
                    "type": self.peer.localDescription.type,
                },
            }
        )

    async def apply_webrtc_answer(self, answer: dict[str, str]):
        if self.peer is not None:
            await self.peer.setRemoteDescription(
                RTCSessionDescription(answer["sdp"], answer["type"])
            )

    async def consume_video(self, track):
        try:
            while True:
                frame = await track.recv()
                self.frames.put(frame.to_image().convert("RGB"))
        except Exception:
            pass

    def start_socket(self):
        self.ws = websocket.WebSocketApp(
            self.server_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        thread.start()

    def source_coordinates(self, event) -> tuple[float, float] | None:
        left, top, width, height = self.image_box
        if event.x < left or event.y < top or event.x > left + width or event.y > top + height:
            return None
        x = (event.x - left) * self.source_width / width
        y = (event.y - top) * self.source_height / height
        return max(0, x), max(0, y)

    def left_click(self, event):
        coordinates = self.source_coordinates(event)
        if coordinates:
            self.send({
                "type": "mouse",
                "client_id": self.client_id,
                "action": "click",
                "x": coordinates[0],
                "y": coordinates[1],
                "button": "left",
            })

    def right_click(self, event):
        coordinates = self.source_coordinates(event)
        if coordinates:
            self.send({
                "type": "mouse",
                "client_id": self.client_id,
                "action": "right_click",
                "x": coordinates[0],
                "y": coordinates[1],
                "button": "right",
            })

    def scroll(self, event):
        self.send({
            "type": "scroll",
            "client_id": self.client_id,
            "amount": 120 if event.delta > 0 else -120,
        })

    def key_press(self, event):
        if event.keysym in {"Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R"}:
            return
        modifiers = []
        if event.state & 0x0004:
            modifiers.append("ctrl")
        if event.state & 0x0008:
            modifiers.append("alt")
        if event.state & 0x0001:
            modifiers.append("shift")

        key_map = {
            "Up": "ArrowUp",
            "Down": "ArrowDown",
            "Left": "ArrowLeft",
            "Right": "ArrowRight",
            "BackSpace": "Backspace",
            "Delete": "Delete",
            "Return": "Enter",
            "Escape": "Escape",
            "Tab": "Tab",
            "Home": "Home",
            "End": "End",
            "Prior": "PageUp",
            "Next": "PageDown",
        }
        key = key_map.get(event.keysym, event.keysym)
        if modifiers:
            self.send({
                "type": "shortcut",
                "client_id": self.client_id,
                "keys": modifiers + [key.lower()],
            })
        else:
            self.send({
                "type": "keyboard",
                "client_id": self.client_id,
                "key": key,
                "text": event.char if len(event.char) == 1 else "",
            })

    def draw_latest_frame(self):
        latest = None
        while True:
            try:
                latest = self.frames.get_nowait()
            except queue.Empty:
                break
        if latest:
            try:
                if isinstance(latest, Image.Image):
                    image = latest
                else:
                    image = Image.open(io.BytesIO(base64.b64decode(latest["frame"]))).convert("RGB")
                self.source_width, self.source_height = image.size
                available_width = max(1, self.canvas.winfo_width())
                available_height = max(1, self.canvas.winfo_height())
                scale = min(available_width / image.width, available_height / image.height)
                display_size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
                image = image.resize(display_size, Image.Resampling.LANCZOS)
                self.photo = ImageTk.PhotoImage(image)
                left = (available_width - display_size[0]) / 2
                top = (available_height - display_size[1]) / 2
                self.image_box = (left, top, display_size[0], display_size[1])
                self.canvas.delete("all")
                self.canvas.create_image(left, top, image=self.photo, anchor=tk.NW)
            except Exception:
                pass
        self.root.after(30, self.draw_latest_frame)

    def close(self):
        if self.ws is not None:
            try:
                self.send({
                    "type": f"{self.mode}_control",
                    "client_id": self.client_id,
                    "action": "close",
                })
                self.ws.close()
            except Exception:
                pass
            self.ws = None
        if self.peer is not None and self.webrtc_loop:
            future = asyncio.run_coroutine_threadsafe(self.peer.close(), self.webrtc_loop)
            try:
                future.result(timeout=2)
            except Exception:
                pass
            self.peer = None
            self.webrtc_loop.call_soon_threadsafe(self.webrtc_loop.stop)
        if self.root.winfo_exists():
            self.root.destroy()

    def run(self):
        self.start_socket()
        self.root.after(30, self.draw_latest_frame)
        self.root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Visualizador nativo da tela remota")
    parser.add_argument("--server", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--mode", choices=("screen", "camera"), default="screen")
    args = parser.parse_args()
    ScreenViewer(args.server, args.client_id, args.title, args.mode).run()
