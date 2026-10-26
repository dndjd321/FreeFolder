"""Synchronous JSONL client; the caller owns this worker and its battle state.

FastAPI can use ``asyncio.to_thread`` around calls. A worker failure never retries
an action implicitly: restore an acknowledged checkpoint before continuing.
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any


class BattleServiceError(RuntimeError):
    def __init__(self, message: str, code: str = "ENGINE_ERROR"):
        super().__init__(message)
        self.code = code


class BattleClient:
    """Own one local Node worker with serialized requests and bounded waits."""

    def __init__(self, *, timeout: float = 15.0, node: str | None = None):
        self.timeout = timeout
        self.node = node or shutil.which("node")
        if not self.node:
            raise BattleServiceError("Node.js is required for the battle service", "NODE_MISSING")
        self._lock = threading.RLock()
        self._next_id = 0
        self._closed = False
        self._responses: queue.Queue[Any] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=30)
        self._process = subprocess.Popen(
            [self.node, str(Path(__file__).with_name("worker.cjs"))],
            cwd=str(Path(__file__).parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            self.info = self.request("ping")
        except Exception:
            self.close()
            raise

    @property
    def pid(self) -> int:
        return self._process.pid

    def _read_stdout(self):
        try:
            for line in self._process.stdout:
                try:
                    self._responses.put(json.loads(line))
                except (ValueError, TypeError) as exc:
                    self._responses.put(exc)
                    break
        finally:
            self._responses.put(None)

    def _read_stderr(self):
        for line in self._process.stderr:
            self._stderr.append(line.rstrip())

    def request(self, op: str, **payload) -> dict:
        with self._lock:
            if self._closed or self._process.poll() is not None:
                raise BattleServiceError("Battle worker is closed", "WORKER_CLOSED")
            self._next_id += 1
            request_id = self._next_id
            try:
                self._process.stdin.write(json.dumps({"id": request_id, "op": op, **payload}, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
                response = self._responses.get(timeout=self.timeout)
            except queue.Empty as exc:
                self.close()
                raise BattleServiceError("Battle worker timed out; action outcome is unknown", "WORKER_TIMEOUT") from exc
            except (BrokenPipeError, OSError) as exc:
                self.close()
                raise BattleServiceError("Battle worker disconnected", "WORKER_CLOSED") from exc
            if response is None or isinstance(response, Exception):
                detail = "; ".join(self._stderr)
                self.close()
                raise BattleServiceError("Battle worker exited or returned invalid JSON" + (": " + detail if detail else ""), "WORKER_PROTOCOL")
            if response.get("id") != request_id:
                self.close()
                raise BattleServiceError("Battle worker response ID mismatch", "WORKER_PROTOCOL")
            if not response.get("ok"):
                error = response.get("error", {})
                raise BattleServiceError(error.get("message", "Battle request failed"), error.get("code", "ENGINE_ERROR"))
            return response["result"]

    def create(self, teams: dict, *, seed=None, initial_state=None, names=None) -> dict:
        return self.request("create", teams=teams, seed=seed, initial_state=initial_state, names=names)

    def choose(self, battle_id: str, side: str, action: dict) -> dict:
        return self.request("choose", battle_id=battle_id, side=side, action=action)

    def view(self, battle_id: str, side: str) -> dict:
        return self.request("view", battle_id=battle_id, side=side)

    def resources(self, battle_id: str) -> dict:
        return self.request("resources", battle_id=battle_id)

    def checkpoint(self, battle_id: str) -> dict:
        return self.request("checkpoint", battle_id=battle_id)

    def restore(self, checkpoint: dict) -> dict:
        return self.request("restore", checkpoint=checkpoint)

    def replay(self, replay_data: dict) -> dict:
        return self.request("replay", replay=replay_data)

    def close_battle(self, battle_id: str) -> dict:
        return self.request("close_battle", battle_id=battle_id)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._process.poll() is None:
                try:
                    self._process.stdin.close()
                    self._process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    self._process.kill()
                    self._process.wait(timeout=2)
            for pipe in (self._process.stdout, self._process.stderr):
                if pipe:
                    pipe.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
