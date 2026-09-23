"""Lazy lifecycle of independently authenticated local analysis/scheduling workers."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

import httpx

from pen import config
from pen.practice.contracts import PROTOCOL, timestamp


def enabled(scope: str) -> bool:
    path = config.PEN_DIR / "practice-enabled" / f"{scope}.json"
    try:
        return json.loads(path.read_text()).get("enabled") is True
    except (OSError, ValueError, AttributeError):
        return False


def set_enabled(scope: str, value: bool) -> None:
    dest = config.PEN_DIR / "practice-enabled" / f"{scope}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps({"enabled": value, "updated_at": timestamp()}))
    os.replace(tmp, dest)
    if not value and not any(enabled(p.stem) for p in dest.parent.glob("*.json")):
        supervisor.stop()


class ServiceUnavailable(RuntimeError):
    pass


class Supervisor:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.children: dict[str, dict[str, Any]] = {}
        self.owner = uuid.uuid4().hex
        self.home: str | None = None

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {name: {"status": "running" if rec["process"].poll() is None else "stopped",
                           "pid": rec["process"].pid, "protocol": PROTOCOL}
                    for name, rec in self.children.items()}

    def ensure(self, service: str) -> dict[str, Any]:
        if service not in {"analysis", "scheduling"}:
            raise ValueError("unknown service")
        with self.lock:
            home = str(config.PEN_DIR.resolve())
            if self.home is not None and self.home != home:
                self.stop()
            self.home = home
            old = self.children.get(service)
            if old and old["process"].poll() is None:
                return old
            directory = config.PEN_DIR / "practice" / "services"
            directory.mkdir(parents=True, exist_ok=True)
            ready = directory / f"{service}-{self.owner}.json"
            ready.unlink(missing_ok=True)
            token = secrets.token_urlsafe(32)
            env = {**os.environ, "PEN_PRACTICE_TOKEN": token, "PEN_HOME": home}
            log = (directory / f"{service}.log").open("ab")
            try:
                proc = subprocess.Popen([
                    sys.executable, "-m", "pen.practice.worker", "--service", service,
                    "--parent-pid", str(os.getpid()), "--ready-file", str(ready), "--pen-home", home,
                ], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            finally:
                log.close()
            rec = {"process": proc, "token": token, "ready": ready, "port": None}
            self.children[service] = rec
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    data = json.loads(ready.read_text())
                    if data.get("pid") != proc.pid or data.get("protocol") != PROTOCOL:
                        raise ServiceUnavailable("worker identity/protocol mismatch")
                    rec["port"] = int(data["port"])
                    response = self._request(rec, "GET", "/v1/health")
                    if response.get("service") == service:
                        return rec
                except (OSError, ValueError, httpx.HTTPError):
                    pass
                except ServiceUnavailable:
                    break
                time.sleep(.05)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            self.children.pop(service, None)
            raise ServiceUnavailable(f"{service} did not become ready; see {directory / (service + '.log')}")

    @staticmethod
    def _request(rec: dict[str, Any], method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=60, trust_env=False) as client:
            response = client.request(method, f"http://127.0.0.1:{rec['port']}{path}", json=body,
                                      headers={"Authorization": f"Bearer {rec['token']}"})
            response.raise_for_status()
            return response.json()

    def call(self, service: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        rec = self.ensure(service)
        try:
            return self._request(rec, "POST", path, body)
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceUnavailable(f"{service}: {exc}") from exc

    def stop(self) -> None:
        with self.lock:
            for rec in self.children.values():
                proc = rec["process"]
                if proc.poll() is None:
                    proc.terminate()
            for rec in self.children.values():
                proc = rec["process"]
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                rec["ready"].unlink(missing_ok=True)
            self.children.clear()


supervisor = Supervisor()
atexit.register(supervisor.stop)
