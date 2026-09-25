import argparse
import asyncio
import json
import subprocess
import sys
import websockets

TARGET_CLIENTS = {}
ADMIN_CONTROLLERS = {}

async def notify_admins():
    client_list = []
    for c_id, data in TARGET_CLIENTS.items():
        info = data.get("info", {})
        client_list.append({
            "client_id": c_id,
            "hostname": info.get("hostname", "-"),
            "user": info.get("user", "-"),
            "platform": info.get("platform", "-")
        })
    
    payload = json.dumps({"type": "client_list_update", "clients": client_list})
    for admin_ws in list(ADMIN_CONTROLLERS.values()):
        try:
            await admin_ws.send(payload)
        except Exception:
            pass

async def handler(websocket):
    client_type = None
    client_id = None
    
    try:
        async for message in websocket:
            data = json.loads(message)
            msg_type = data.get("type")

            if msg_type == "register":
                client_id = data.get("client_id")
                role = data.get("role", "target")
                
                if role == "admin":
                    client_type = "admin"
                    ADMIN_CONTROLLERS[client_id] = websocket
                    print(f"[+] Painel Controlador (Admin) conectado: {client_id}")
                    await notify_admins()
                else:
                    client_type = "target"
                    TARGET_CLIENTS[client_id] = {"websocket": websocket, "info": data}
                    print(f"[+] Dispositivo Alvo conectado: {client_id}")
                    await notify_admins()

            elif client_type == "admin":
                target_id = data.get("target_id")
                if target_id in TARGET_CLIENTS:
                    await TARGET_CLIENTS[target_id]["websocket"].send(json.dumps(data))

            elif client_type == "target":
                payload = json.dumps(data)
                for admin_ws in list(ADMIN_CONTROLLERS.values()):
                    try:
                        await admin_ws.send(payload)
                    except Exception:
                        pass

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if client_type == "admin" and client_id in ADMIN_CONTROLLERS:
            del ADMIN_CONTROLLERS[client_id]
            print(f"[-] Painel Controlador desconectado: {client_id}")
        elif client_type == "target" and client_id in TARGET_CLIENTS:
            del TARGET_CLIENTS[client_id]
            print(f"[-] Dispositivo Alvo desconectado: {client_id}")
            await notify_admins()

async def main(host, port, open_admin):
    print(f"==========================================")
    print(f" RemoteDesk Server")
    print(f" Executando em ws://{host}:{port}")
    print(f"==========================================")

    # Se o parâmetro --admin foi passado, inicia o remotedesk.py em subprocesso
    if open_admin:
        print("[*] Iniciando interface gráfica do RemoteDesk...")
        subprocess.Popen([sys.executable, "remotedesk.py", "--host", host, "--port", str(port)])

    async with websockets.serve(handler, host, port, max_size=None):
        await asyncio.Future()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RemoteDesk Server")
    parser.add_argument("--host", default="0.0.0.0", help="IP do servidor")
    parser.add_argument("--port", type=int, default=8000, help="Porta do servidor")
    parser.add_argument("--admin", action="store_true", help="Inicia o servidor e abre a interface gráfica remotedesk.py")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.host, args.port, args.admin))
    except KeyboardInterrupt:
        print("\nServidor finalizado.")
        sys.exit(0)