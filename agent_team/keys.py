"""Provider API keys. Kept in memory by default. Saved only on request, encrypted with a master key kept outside the data directory."""

import json
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

FORMAT_VERSION = 1


class KeyVaultError(RuntimeError):
    pass


def default_master_key_path():
    configured = os.environ.get("AGENT_TEAM_MASTER_KEY_FILE")
    return Path(configured) if configured else Path.home() / ".config" / "agent_team" / "master.key"


def mask(key):
    return "••••" + key[-4:]


def _create_master_key(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(key + b"\n")
    return key


class KeyVault:
    """Holds provider keys. Pass data_dir=None to disable saving keys to disk."""

    def __init__(self, data_dir=None, master_key_path=None):
        self.file = Path(data_dir) / "keys.json" if data_dir else None
        self.master_key_path = Path(master_key_path) if master_key_path else default_master_key_path()
        self.lock = threading.Lock()
        self.memory = {}  # provider -> key, this session only
        self.saved = {}  # provider -> {"ciphertext": str, "hint": str}
        self._cipher = None
        if self.file and self.file.exists():
            self.saved = json.loads(self.file.read_text())

    def get(self, provider):
        with self.lock:
            if provider in self.memory:
                return self.memory[provider]
            entry = self.saved.get(provider)
            return self._decrypt(entry["ciphertext"]) if entry else None

    def set(self, provider, key, persist=False):
        key = key.strip()
        if not key:
            raise KeyVaultError("the key is empty")
        with self.lock:
            if persist:
                if self.file is None:
                    raise KeyVaultError("this app has no data directory, so keys cannot be saved")
                self.saved[provider] = {"ciphertext": self._encrypt(key), "hint": mask(key)}
                self.memory.pop(provider, None)
            else:
                self.memory[provider] = key
                self.saved.pop(provider, None)
            self._write()

    def delete(self, provider):
        with self.lock:
            self.memory.pop(provider, None)
            self.saved.pop(provider, None)
            self._write()

    def secret_values(self):
        """Every key this vault can give out, for redacting logs and errors. Keys that cannot be read are skipped."""
        with self.lock:
            names = set(self.memory) | set(self.saved)
        values = []
        for name in names:
            try:
                values.append(self.get(name))
            except KeyVaultError:
                pass
        return [value for value in values if value]

    def status(self):
        """Masked view only. Full keys never leave this class through this method."""
        with self.lock:
            result = {}
            for name in sorted(set(self.memory) | set(self.saved)):
                if name in self.memory:
                    result[name] = {"persisted": False, "hint": mask(self.memory[name])}
                else:
                    result[name] = {"persisted": True, "hint": self.saved[name]["hint"]}
            return result

    def _encrypt(self, key):
        token = self._fernet(create=True).encrypt(key.encode()).decode()
        return f"v{FORMAT_VERSION}:{token}"

    def _decrypt(self, ciphertext):
        version, _, token = ciphertext.partition(":")
        if version != f"v{FORMAT_VERSION}":
            raise KeyVaultError("a saved key uses an unknown format")
        try:
            return self._fernet(create=False).decrypt(token.encode()).decode()
        except InvalidToken:
            raise KeyVaultError("a saved key cannot be decrypted with the current master key; enter it again") from None

    def _fernet(self, create):
        if self._cipher is None:
            path = self.master_key_path
            if path.exists():
                key = path.read_bytes().strip()
            elif create:
                key = _create_master_key(path)
            else:
                raise KeyVaultError(f"master key file {path} is missing; saved keys cannot be read")
            self._cipher = Fernet(key)
        return self._cipher

    def _write(self):
        if self.file is None:
            return
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.file.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            json.dump(self.saved, file)
        os.replace(temporary, self.file)
