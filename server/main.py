import asyncio
import contextlib
import hmac
import json
import os
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from server import config
from server.decision_engine import get_model
from server.detector import LABEL_VALUES, current_label, labels_payload, set_label
from server.registry import GUARD, MONITOR, DeviceRegistry, other_role

WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
DASHBOARD_HTML = os.path.join(WEB_DIR, "dashboard.html")

MAX_AUTH_FAILURES = 5
AUTH_BLOCK_SECONDS = 60.0

auth_failures = {}
registry = DeviceRegistry()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async def background_tasks():
        while True:
            await asyncio.sleep(1.0)
            for alarm in registry.check_timeouts():
                print(f"[ALARM WARN] {alarm['reason']}")
                await registry.broadcast_alarm(alarm)

            await registry.broadcast_peer_updates()

    get_model()  # muat model sekali saat server mulai
    task = asyncio.create_task(background_tasks())
    yield
    task.cancel()


app = FastAPI(title="TasGuard Server", lifespan=lifespan)


@app.middleware("http")
async def no_cache_middleware(request: Request, call_next):
    """Halaman dan skrip tidak boleh di-cache agar HP selalu memakai versi terbaru."""
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".html", ".js", ".css")) or path == "/" or path == "/dashboard":
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


def is_local_request(request: Request) -> bool:
    """Dashboard hanya boleh diakses dari localhost (laptop)."""
    if request.headers.get("cf-connecting-ip"):
        return False
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


@app.get("/dashboard")
async def dashboard_page(request: Request):
    if not is_local_request(request):
        raise HTTPException(status_code=403, detail="Dashboard hanya dapat diakses secara lokal dari laptop.")
    if os.path.exists(DASHBOARD_HTML):
        return FileResponse(DASHBOARD_HTML)
    raise HTTPException(status_code=404, detail="dashboard.html belum ada.")


@app.get("/api/status")
async def api_status(request: Request):
    if not is_local_request(request):
        return JSONResponse(status_code=403, content={"detail": "Akses ditolak (local-only)."})
    return registry.status_api()


@app.post("/api/label")
async def api_label(request: Request, name: str = ""):
    """Tandai kondisi yang sedang direkam (hanya laptop). name kosong = hapus label."""
    if not is_local_request(request):
        return JSONResponse(status_code=403, content={"detail": "Akses ditolak (local-only)."})
    set_label(name)
    return {"label": current_label()}


@app.get("/api/label")
async def api_label_get(request: Request):
    if not is_local_request(request):
        return JSONResponse(status_code=403, content={"detail": "Akses ditolak (local-only)."})
    return {"label": current_label()}


def client_ip(websocket: WebSocket) -> str:
    forwarded = websocket.headers.get("cf-connecting-ip")
    if forwarded:
        return forwarded
    return websocket.client.host if websocket.client else "unknown"


def is_blocked(ip: str) -> bool:
    data = auth_failures.get(ip)
    if not data:
        return False
    if data["blocked_until"] > time.time():
        return True
    if data["blocked_until"]:
        auth_failures.pop(ip, None)
    return False


def register_failure(ip: str) -> None:
    data = auth_failures.setdefault(ip, {"count": 0, "blocked_until": 0.0})
    data["count"] += 1
    if data["count"] >= MAX_AUTH_FAILURES:
        data["blocked_until"] = time.time() + AUTH_BLOCK_SECONDS


async def send_json(websocket: WebSocket, payload: dict) -> None:
    await websocket.send_text(json.dumps(payload))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    ip = client_ip(websocket)
    await websocket.accept()

    if is_blocked(ip):
        await send_json(websocket, {"type": "auth_fail", "reason": "blocked"})
        await websocket.close(code=4003, reason="Too many failed attempts")
        return

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
    except asyncio.TimeoutError:
        await send_json(websocket, {"type": "auth_fail", "reason": "timeout"})
        await websocket.close(code=4001, reason="Auth timeout")
        return

    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        message = {}

    provided = str(message.get("key", ""))
    expected = config.ACCESS_KEY
    device_id = str(message.get("device_id", "")).strip()

    if message.get("type") != "auth" or not expected or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ) or not device_id:
        register_failure(ip)
        await send_json(websocket, {"type": "auth_fail", "reason": "key"})
        await websocket.close(code=4001, reason="Invalid access key or device_id")
        return

    # Kode akses benar; sekarang peran yang dipilih HP ini.
    role = str(message.get("role", "")).strip().upper()
    dev, old_ws, error = registry.assign(device_id, websocket, role)
    auth_failures.pop(ip, None)
    if not dev:
        await send_json(websocket, {"type": "auth_fail", "reason": error})
        await websocket.close(code=4004 if error == "peran_terpakai" else 4002, reason=f"Role rejected: {error}")
        print(f"[AUTH] Peran '{role}' ditolak ({error}) untuk device_id={device_id} dari {ip}")
        return

    if old_ws and old_ws is not websocket:
        try:
            await old_ws.close(code=4000, reason="Replaced by same device reconnection")
        except Exception:
            pass

    await send_json(
        websocket,
        {
            "type": "auth_ok",
            "role": role,
            "status": dev.status,
            "peer": registry.snapshot(other_role(role)),
            "self": registry.snapshot(role),
            "labels": labels_payload(),
            "label": current_label(),
        },
    )
    print(f"[HP {role}] Terhubung sebagai {'pemantau' if role == MONITOR else 'penjaga'} (device_id={device_id})")
    await registry.broadcast_peer_updates()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = message.get("type")
            registry.touch(role)

            if kind == "ping":
                ping_ts = float(message.get("ts", time.time()))
                await send_json(
                    websocket,
                    {
                        "type": "pong",
                        "ping_ts": ping_ts,
                        "server_ts": round(time.time(), 3),
                    },
                )

            elif kind == "guard" and role == GUARD:
                active = bool(message.get("active"))
                changed = registry.handle_guard(role, active)
                print(f"[HP {role}] Penjagaan {'DIMULAI' if active else 'DIHENTIKAN'}")
                if changed and current_label():
                    set_label("")   # sesi penjagaan baru dimulai tanpa label yang tersisa
                    print("[LABEL] Label dikosongkan karena status penjagaan berubah")
                await registry.broadcast_peer_updates()

            elif kind == "sensor" and role == GUARD:
                alarm = registry.handle_sensor(role, message)
                if alarm:
                    ai = alarm.get("ai") or {}
                    print(f"[ALARM] {alarm['level']} - {alarm['reason']} | skor AI={ai.get('score')} | "
                          f"total={dev.alarms}")
                    await registry.broadcast_alarm(alarm)

            elif kind == "location" and role == GUARD:
                registry.handle_location(role, message)

            elif kind == "label" and role == MONITOR:
                name = str(message.get("name", ""))
                if name == "" or name in LABEL_VALUES:
                    set_label(name)
                    print(f"[LABEL] Rekam: {name or '(tanpa label)'} (dari HP A)")
                    await registry.broadcast_peer_updates()

            elif kind == "dismiss" and role == MONITOR:
                registry.dismiss()
                print(f"[HP {role}] Peringatan ditutup (Saya Sudah Aman)")
                await registry.broadcast_peer_updates()

            # Pesan lain (mis. sensor dari HP pemantau) diabaikan: arah data sensor hanya B -> A.

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        print(f"[WS {role}] Error: {exc}")
    finally:
        registry.release(role, websocket)
        print(f"[HP {role}] Terputus")
        await registry.broadcast_peer_updates()


if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
