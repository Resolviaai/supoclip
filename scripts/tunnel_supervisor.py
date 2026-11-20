"""
Dispatch (SupoClip) Permanent Auto-Healing Tunnel & Engine Supervisor
Ensures Docker, containers, and Cloudflare Tunnel remain online 24/7.
Automatically syncs dynamic tunnel URLs to Netlify in real-time.
"""

import os
import re
import sys
import time
import subprocess
import urllib.request
import urllib.error

WORKSPACE_DIR = r"C:\CODE\supoclip"
NETLIFY_DIR = os.path.join(WORKSPACE_DIR, r"deploy\netlify-proxy")
CLOUDFLARED_EXE = r"C:\Program Files (x86)\cloudflared\cloudflared.exe"
LOG_FILE = os.path.join(WORKSPACE_DIR, r"scripts\cloudflared.log")
SUPERVISOR_LOG = os.path.join(WORKSPACE_DIR, r"scripts\supervisor.log")

LOCAL_HEALTH_URL = "http://localhost:3107"
PUBLIC_HEALTH_URL = "https://dispatch.theresolvia.com"

current_tunnel_url = None
cloudflared_proc = None

def log(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [Supervisor] {msg}"
    print(line, flush=True)
    try:
        with open(SUPERVISOR_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def check_http(url: str, timeout: int = 5) -> int:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "DispatchSupervisor/1.0", "ngrok-skip-browser-warning": "1"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0

def ensure_docker():
    try:
        res = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            return True
    except Exception:
        pass

    log("Docker engine not ready. Launching Docker Desktop...")
    docker_backend = r"C:\Program Files\Docker\Docker\resources\com.docker.backend.exe"
    docker_app = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
    
    if os.path.exists(docker_backend):
        subprocess.Popen([docker_backend], creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    elif os.path.exists(docker_app):
        subprocess.Popen([docker_app], creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    for i in range(30):
        time.sleep(2)
        try:
            res = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                log("Docker engine is now online!")
                return True
        except Exception:
            pass
    log("Warning: Docker engine wait timed out.")
    return False

def ensure_containers():
    code = check_http(LOCAL_HEALTH_URL, timeout=3)
    if code in (200, 307, 308):
        return True
    
    log(f"Local web service not responding (code={code}). Running docker compose up -d...")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=WORKSPACE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60
        )
        for _ in range(15):
            time.sleep(2)
            if check_http(LOCAL_HEALTH_URL, timeout=3) in (200, 307, 308):
                log("Docker containers are up and healthy!")
                return True
    except Exception as e:
        log(f"Error starting containers: {e}")
    return False

def extract_tunnel_url(tail_lines: int = 50) -> str | None:
    if not os.path.exists(LOG_FILE):
        return None
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-tail_lines:]
        content = "".join(lines)
        matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
        if matches:
            return matches[-1]
    except Exception:
        pass
    return None

def start_cloudflared():
    global cloudflared_proc
    if not os.path.exists(CLOUDFLARED_EXE):
        log(f"Error: {CLOUDFLARED_EXE} not found.")
        return None

    # Check if existing cloudflared is already running
    try:
        tasks = subprocess.check_output('tasklist /FI "IMAGENAME eq cloudflared.exe"', shell=True).decode()
        if "cloudflared.exe" in tasks:
            url = extract_tunnel_url(20)
            if url:
                return url
    except Exception:
        pass

    log("Starting cloudflared quick tunnel...")
    # Rotate log if too big
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
        try:
            os.remove(LOG_FILE)
        except Exception:
            pass

    cmd = [
        CLOUDFLARED_EXE,
        "tunnel",
        "--url",
        "http://localhost:3107",
        "--logfile",
        LOG_FILE,
        "--protocol",
        "quic"
    ]
    cloudflared_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    )

    for _ in range(20):
        time.sleep(1)
        url = extract_tunnel_url()
        if url:
            log(f"Tunnel established: {url}")
            return url
    return None

def sync_to_netlify(url: str):
    log(f"Syncing new UPSTREAM_ORIGIN to Netlify: {url}...")
    try:
        res = subprocess.run(
            ["npx.cmd", "--yes", "netlify", "env:set", "UPSTREAM_ORIGIN", url],
            cwd=NETLIFY_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        if res.returncode == 0:
            log(f"Successfully synced {url} to Netlify!")
            return True
        else:
            log(f"Netlify env:set failed with code {res.returncode}: {res.stdout.strip()}")
    except Exception as e:
        log(f"Error running netlify env:set: {e}")
    return False

def main_loop():
    global current_tunnel_url
    log("Starting Dispatch Permanent Supervisor...")
    ensure_docker()
    ensure_containers()

    # Discover or start tunnel
    tunnel_url = start_cloudflared()
    if tunnel_url:
        current_tunnel_url = tunnel_url
        # Check if Netlify responds 200
        pub_code = check_http(PUBLIC_HEALTH_URL, timeout=8)
        log(f"Initial public check: {PUBLIC_HEALTH_URL} -> HTTP {pub_code}")
        if pub_code != 200:
            sync_to_netlify(tunnel_url)
            time.sleep(5)
            log(f"Post-sync public check: {PUBLIC_HEALTH_URL} -> HTTP {check_http(PUBLIC_HEALTH_URL, timeout=8)}")

    failures = 0
    while True:
        try:
            time.sleep(20)
            
            # 1. Health check local containers
            loc_code = check_http(LOCAL_HEALTH_URL, timeout=3)
            if loc_code not in (200, 307, 308):
                log(f"Local health check warning: HTTP {loc_code}. Ensuring containers...")
                ensure_containers()

            # 2. Check if cloudflared is running
            tasks = subprocess.check_output('tasklist /FI "IMAGENAME eq cloudflared.exe"', shell=True).decode()
            if "cloudflared.exe" not in tasks:
                log("cloudflared is not running! Relaunching...")
                new_url = start_cloudflared()
                if new_url and new_url != current_tunnel_url:
                    current_tunnel_url = new_url
                    sync_to_netlify(new_url)
                continue

            # 3. Check public health
            pub_code = check_http(PUBLIC_HEALTH_URL, timeout=8)
            if pub_code == 200:
                failures = 0
            else:
                failures += 1
                log(f"Public health check returned {pub_code} (failure count: {failures})")
                if failures >= 2:
                    log("Public endpoint failing repeatedly. Checking tunnel status...")
                    active_url = extract_tunnel_url()
                    if active_url and active_url != current_tunnel_url:
                        log(f"Detected new tunnel URL in logs: {active_url}")
                        current_tunnel_url = active_url
                        sync_to_netlify(active_url)
                        failures = 0
                    else:
                        log("Restarting cloudflared tunnel to re-establish route...")
                        subprocess.run('taskkill /F /IM cloudflared.exe', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(2)
                        new_url = start_cloudflared()
                        if new_url:
                            current_tunnel_url = new_url
                            sync_to_netlify(new_url)
                        failures = 0

        except KeyboardInterrupt:
            log("Supervisor stopped by user.")
            sys.exit(0)
        except Exception as e:
            log(f"Supervisor error in main loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main_loop()
