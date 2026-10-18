"""Tools agents can be given. Files live in a workspace folder per run. Fetching reads http and https pages.

Each group is opt-in per team member: "files" or "fetch" in the member's "tools" list.
"""

import html
import http.client
import ipaddress
import os
import re
import shutil
import socket
import ssl
from pathlib import Path
from urllib.parse import urljoin, urlsplit

MAX_READ = 100_000
MAX_WRITE = 200_000
MAX_FETCH = 200_000
MAX_LIST = 200
MAX_REDIRECTS = 5
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
GROUPS = {
    "files": ("list_files", "read_file", "write_file"),
    "fetch": ("fetch_url",),
}

LIST_FILES = {
    "name": "list_files",
    "description": "List the files in your run's workspace, as paths relative to the workspace.",
    "input_schema": {"type": "object", "properties": {}},
}
READ_FILE = {
    "name": "read_file",
    "description": "Read a text file from the workspace.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path relative to the workspace"}},
        "required": ["path"],
    },
}
WRITE_FILE = {
    "name": "write_file",
    "description": "Create or replace a text file in the workspace. Parent folders are created as needed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
}
FETCH_URL = {
    "name": "fetch_url",
    "description": "Fetch a web page over http or https and return its text. Private addresses are refused.",
    "input_schema": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
}
DEFINITIONS = {tool["name"]: tool for tool in (LIST_FILES, READ_FILE, WRITE_FILE, FETCH_URL)}


class ToolError(Exception):
    """A problem the model can fix, reported back to it as an error result."""


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative):
        if not isinstance(relative, str) or not relative.strip():
            raise ToolError("give a file path inside the workspace")
        if os.path.isabs(relative):
            raise ToolError("use a path relative to the workspace, not an absolute path")
        current = self.root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ToolError(f"{relative!r} goes through a link, and links are not allowed")
        resolved = (self.root / relative).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ToolError(f"{relative!r} is outside the workspace")
        return resolved

    def list_files(self):
        paths = sorted(
            str(path.relative_to(self.root))
            for path in self.root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        if len(paths) > MAX_LIST:
            return paths[:MAX_LIST] + [f"... and {len(paths) - MAX_LIST} more"]
        return paths

    def read(self, relative):
        path = self.resolve(relative)
        if not path.is_file():
            raise ToolError(f"there is no file named {relative!r}")
        data = path.read_bytes()
        text = data[:MAX_READ].decode("utf-8", errors="replace")
        if len(data) > MAX_READ:
            text += f"\n[The file continues. Only the first {MAX_READ} bytes are shown.]"
        return text

    def write(self, relative, content):
        if len(content.encode("utf-8")) > MAX_WRITE:
            raise ToolError(f"the file is larger than {MAX_WRITE} bytes; split it into several files")
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {relative}."


def copy_workspace(source, destination):
    """Copy a run's workspace for a branch. Symbolic links are skipped, as they are everywhere in a workspace."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        return
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=lambda directory, names: [name for name in names if os.path.islink(os.path.join(directory, name))],
    )


def check_url(url, allow_private=False):
    """Refuses a non-public address. Returns the address to connect to, which is one of the addresses just checked,
    so a second lookup cannot send the connection somewhere else (DNS rebinding). Returns None when private
    addresses are allowed; the connection then uses the name."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ToolError("only http and https addresses can be fetched")
    if not parts.hostname:
        raise ToolError("the address has no host name")
    try:
        port = parts.port or 0
    except ValueError:
        raise ToolError("the address has an invalid port") from None
    if allow_private:
        return None
    try:
        addresses = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ToolError(f"could not look up {parts.hostname}") from None
    checked = []
    for *_, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0].split("%")[0])
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped  # ::ffff:127.0.0.1 is the loopback address too
        if address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise ToolError(f"{parts.hostname} is a private address, which cannot be fetched")
        checked.append(str(address))
    if not checked:
        raise ToolError(f"could not look up {parts.hostname}")
    return checked[0]


def html_to_text(markup):
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", markup))
    return re.sub(r"\s+", " ", text).strip()


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Connects to a checked address. The host name still goes in the Host header."""

    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout)
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to a checked address, and checks the certificate against the host name, as for any HTTPS request."""

    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _get(url, address, timeout):
    """One request, with no redirects followed. Returns (status, headers, body bytes up to the limit)."""
    parts = urlsplit(url)
    secure = parts.scheme == "https"
    port = parts.port or (443 if secure else 80)
    connection_class = _PinnedHTTPSConnection if secure else _PinnedHTTPConnection
    connection = connection_class(parts.hostname, port, address or parts.hostname, timeout)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        connection.request("GET", target, headers={"User-Agent": "agent-team/1.0"})
        response = connection.getresponse()
        status, headers = response.status, response.headers
        body = b"" if status in REDIRECT_STATUSES else response.read(MAX_FETCH + 1)
    finally:
        connection.close()
    return status, headers, body


def fetch(url, allow_private=False, timeout=20):
    """Reads a page as text. Every hop, including each redirect, is checked and connected to on its own."""
    for _ in range(MAX_REDIRECTS + 1):
        address = check_url(url, allow_private)
        try:
            status, headers, data = _get(url, address, timeout)
        except (OSError, http.client.HTTPException) as error:
            raise ToolError(f"could not fetch the page: {error}") from None
        if status in REDIRECT_STATUSES:
            location = headers.get("location")
            if not location:
                raise ToolError("the page redirected without saying where to")
            url = urljoin(url, location)
            continue
        if status >= 400:
            raise ToolError(f"could not fetch the page: HTTP {status}")
        break
    else:
        raise ToolError(f"the page redirected more than {MAX_REDIRECTS} times")
    charset = headers.get_content_charset() or "utf-8"
    text = data[:MAX_FETCH].decode(charset, errors="replace")
    if "html" in headers.get("content-type", ""):
        text = html_to_text(text)
    if len(data) > MAX_FETCH:
        text += f"\n[The page continues. Only the first {MAX_FETCH} bytes are shown.]"
    return text


def build_tools(workspace_root, allow_private=False):
    """Returns {tool name: (definition, handler)}. A handler takes the call's arguments and returns (text, is_error)."""
    workspace = Workspace(workspace_root)

    def guarded(action):
        def handler(arguments):
            try:
                return action(arguments), False
            except ToolError as error:
                return str(error), True
            except OSError as error:
                return f"could not do that: {error.strerror or error}", True
            except UnicodeError as error:
                return f"the text could not be written as UTF-8 ({error.reason})", True

        return handler

    handlers = {
        "list_files": lambda args: "\n".join(workspace.list_files()) or "The workspace is empty.",
        "read_file": lambda args: workspace.read(args.get("path")),
        "write_file": lambda args: workspace.write(args.get("path"), str(args.get("content", ""))),
        "fetch_url": lambda args: fetch(str(args.get("url", "")), allow_private=allow_private),
    }
    return {name: (DEFINITIONS[name], guarded(action)) for name, action in handlers.items()}
