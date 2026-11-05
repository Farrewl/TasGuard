import argparse
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SERVER_DIR = ROOT_DIR / "server"
VENV_DIR = ROOT_DIR / ".venv"
REQ_FILE = SERVER_DIR / "requirements.txt"
ENV_FILE = ROOT_DIR / ".env"
DEPS_MARKER = VENV_DIR / ".deps_ok"

# Nilai awal konfigurasi. Semua bisa diubah lewat .env tanpa menyentuh kode.
DEFAULT_ENV = {
    "PORT": "8000",
    "ACCESS_KEY": "",
    "LUX_THRESHOLD": "15",
    "LUMA_THRESHOLD": "40",
    "LIGHT_HOLD_MS": "500",
    "ACCEL_THRESHOLD": "2.5",
    "SHAKE_MIN_SAMPLES": "3",
    "COINCIDENCE_WINDOW_S": "2",
    "ALARM_COOLDOWN_S": "30",
    "SENSOR_TIMEOUT_S": "10",
    "LOG_CSV": "true",
    "AI_ENABLED": "true",
    "AI_CALIBRATION_S": "5",
    "AI_COOLDOWN_S": "10",
    "AI_REQUIRE_LIGHT": "true",
}


# Virtual environment

def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        return False


def dependencies_need_install() -> bool:
    if not DEPS_MARKER.exists():
        return True
    return REQ_FILE.stat().st_mtime > DEPS_MARKER.stat().st_mtime


def install_dependencies() -> None:
    """Pasang dependensi; keluaran pip hanya ditampilkan jika gagal."""
    py = venv_python()
    cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQ_FILE)]
    print("Menyiapkan dependensi Python (sekali saja, mohon tunggu)...")
    result = subprocess.run(cmd, cwd=str(ROOT_DIR), capture_output=True, text=True)
    if result.returncode != 0:
        print("Gagal memasang dependensi:")
        print(result.stdout[-2000:])
        print(result.stderr[-2000:])
        sys.exit(1)
    DEPS_MARKER.write_text("ok\n", encoding="utf-8")


def ensure_running_inside_venv() -> None:
    """Jalankan ulang skrip memakai Python dari .venv (agar qrcode dll tersedia)."""
    if in_venv():
        return

    if not VENV_DIR.exists():
        print(f"Membuat virtual environment di {VENV_DIR} ...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)

    if dependencies_need_install():
        install_dependencies()

    # Jalankan ulang skrip dengan Python dari venv
    subprocess.run([str(venv_python()), str(Path(__file__).resolve()), *sys.argv[1:]], check=True)
    sys.exit(0)


# Konfigurasi .env

def read_env() -> dict:
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def write_env(values: dict) -> None:
    lines = [
        "# Konfigurasi TasGuard - JANGAN commit file ini.",
        "# Semua nilai di bawah bisa diubah tanpa menyentuh kode.",
    ]
    for key, default in DEFAULT_ENV.items():
        lines.append(f"{key}={values.get(key, default)}")
    for key, value in values.items():
        if key not in DEFAULT_ENV:
            lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass


def setup_env(new_key: bool) -> dict:
    values = read_env()
    for key, default in DEFAULT_ENV.items():
        values.setdefault(key, default)

    if new_key or not values.get("ACCESS_KEY"):
        values["ACCESS_KEY"] = secrets.token_urlsafe(24)
        print("Kode Akses baru dibuat dan disimpan di .env")

    write_env(values)
    return values


# Port & server

def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def pick_port(preferred: int) -> int:
    if port_is_free(preferred):
        return preferred
    for candidate in range(preferred + 1, preferred + 21):
        if port_is_free(candidate):
            print(f"[INFO] Port {preferred} sedang dipakai proses lain, memakai port {candidate}.")
            return candidate
    print(f"[ERROR] Tidak ada port kosong di rentang {preferred}-{preferred + 20}.")
    sys.exit(1)


def wait_until_healthy(port: int, timeout: float = 20.0) -> bool:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def child_preexec():
    """Di Linux: anak ikut mati jika start.py mati mendadak (mis. kill -9)."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        pass


def stop_process(proc) -> None:
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


# Tunnel

URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def watch_cloudflared(proc, result: dict, done: threading.Event, connected: threading.Event) -> None:
    """Baca keluaran cloudflared di thread terpisah agar pipe tidak penuh.

    ``done`` menyala saat URL diperoleh (atau proses berakhir); ``connected``
    menyala saat tunnel benar-benar tersambung ke jaringan Cloudflare."""
    for line in proc.stdout:
        match = URL_PATTERN.search(line)
        if match and not result.get("url"):
            result["url"] = match.group(0)
            done.set()
        if "Registered tunnel connection" in line:
            connected.set()
        if "ERR" in line:
            text = line.strip()
            if text:
                result.setdefault("errors", []).append(text)
    done.set()


def start_tunnel(port: int):
    if not shutil.which("cloudflared"):
        print("[ERROR] cloudflared tidak ditemukan di PATH.")
        if os.name == "nt":
            print("        Instal dengan: winget install --id Cloudflare.cloudflared")
        else:
            print("        Instal dengan: sudo pacman -S cloudflared")
        sys.exit(1)

    print("Membuka Cloudflare quick tunnel...")
    # HTTP/2 (TCP) dipakai karena protokol bawaan QUIC (UDP) sering diblokir
    # jaringan Wi-Fi rumah/kampus; akibatnya URL terbentuk tetapi tunnel tidak
    # pernah tersambung (Error 1033 di browser).
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}", "--protocol", "http2"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        preexec_fn=child_preexec if os.name == "posix" else None,
    )

    result: dict = {}
    done = threading.Event()
    connected = threading.Event()
    threading.Thread(target=watch_cloudflared, args=(proc, result, done, connected), daemon=True).start()

    deadline = time.time() + 30
    while time.time() < deadline and not result.get("url"):
        if proc.poll() is not None:
            break
        done.wait(0.25)

    if not result.get("url"):
        stop_process(proc)
        print("[ERROR] Gagal mendapatkan URL tunnel dari cloudflared.")
        for err in result.get("errors", [])[-5:]:
            print("       ", err)
        sys.exit(1)

    # URL dicetak cloudflared sebelum tunnel siap; tunggu sampai benar-benar tersambung.
    print("Menunggu tunnel tersambung ke Cloudflare...")
    if not connected.wait(timeout=30):
        print("[PERINGATAN] Tunnel belum tersambung setelah 30 detik, URL di bawah kemungkinan belum bisa dibuka.")
        print("             Periksa koneksi internet atau coba jaringan lain (mis. hotspot HP).")
        for err in result.get("errors", [])[-3:]:
            print("       ", err)

    return proc, result["url"]


# Tampilan

def print_banner(url: str, access_key: str) -> None:
    login_url = f"{url}/?key={access_key}#key={access_key}"
    line = "=" * 68

    print(f"\n{line}")
    print("  TASGUARD SIAP DIPANTAU")
    print(line)
    print(f"  Alamat HTTPS : {url}")
    print(f"  Kode Akses   : {access_key}")
    print(f"  URL + Kode   : {login_url}")
    print(line)

    try:
        import qrcode

        qr = qrcode.QRCode(border=2)
        qr.add_data(login_url)
        qr.make(fit=True)
        print("\n  Scan QR ini dengan kamera HP (Chrome akan terbuka otomatis):\n")
        qr.print_ascii(invert=True)
    except Exception as exc:  # Fitur QR
        print(f"\n  (QR tidak bisa dicetak: {exc})")
        print("  Buka URL + Kode di atas secara manual di Chrome Android.")

    print(f"{line}")
    print("  Tekan Ctrl+C untuk menghentikan server dan tunnel.")
    print(f"{line}\n")


# Program utama

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Jalankan server TasGuard + Cloudflare tunnel.")
    parser.add_argument("--no-tunnel", action="store_true", help="hanya server lokal, tanpa tunnel")
    parser.add_argument("--new-key", action="store_true", help="buat ulang Kode Akses di .env")
    parser.add_argument("--port", type=int, default=None, help="port server (default dari .env)")
    return parser.parse_args()


def main() -> None:
    ensure_running_inside_venv()
    args = parse_args()

    env = setup_env(new_key=args.new_key)
    access_key = env["ACCESS_KEY"]
    preferred_port = args.port if args.port else int(env.get("PORT", "8000"))
    port = pick_port(preferred_port)

    if dependencies_need_install():
        install_dependencies()

    print("Menjalankan server TasGuard...")
    server_proc = subprocess.Popen(
        [str(venv_python()), "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", str(port)],
        cwd=str(ROOT_DIR),
        preexec_fn=child_preexec if os.name == "posix" else None,
    )

    tunnel_proc = None
    try:
        if not wait_until_healthy(port):
            print("[ERROR] Server tidak merespons di /health. Periksa pesan error di atas.")
            return

        if args.no_tunnel:
            local_url = f"http://localhost:{port}"
            print_banner(local_url, access_key)
            print("Mode lokal: HP harus satu Wi-Fi dengan laptop.")
        else:
            tunnel_proc, tunnel_url = start_tunnel(port)
            if not wait_until_healthy(port):
                print("[ERROR] Server berhenti setelah tunnel dibuka.")
                return
            print_banner(tunnel_url, access_key)

        while True:
            time.sleep(1)
            if server_proc.poll() is not None:
                print(f"[ERROR] Server berhenti (kode {server_proc.returncode}).")
                break
            if tunnel_proc and tunnel_proc.poll() is not None:
                print("[ERROR] Proses tunnel berhenti. Jalankan ulang start.py.")
                break

    except KeyboardInterrupt:
        print("\nMenghentikan TasGuard...")
    finally:
        stop_process(tunnel_proc)
        stop_process(server_proc)
        print("TasGuard berhenti dengan bersih.")


def _handle_termination(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, _handle_termination)
    try:
        main()
    except KeyboardInterrupt:
        print("\nDihentikan.")
