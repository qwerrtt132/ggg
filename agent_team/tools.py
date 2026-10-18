"""Tools agents can be given. Files live in a workspace folder per run. Fetching reads http and https pages.

Each group is opt-in per team member: "files" or "fetch" in the member's "tools" list.
"""

import html
import ipaddress
import os
import re
import shutil
import socket
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_READ = 100_000
MAX_WRITE = 200_000
MAX_FETCH = 200_000
MAX_LIST = 200
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
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ToolError("only http and https addresses can be fetched")
    if not parts.hostname:
        raise ToolError("the address has no host name")
    if allow_private:
        return
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or 0, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ToolError(f"could not look up {parts.hostname}") from None
    for *_, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise ToolError(f"{parts.hostname} is a private address, which cannot be fetched")


def html_to_text(markup):
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", markup))
    return re.sub(r"\s+", " ", text).strip()


class CheckedRedirects(HTTPRedirectHandler):
    """Checks every redirect target the same way as the first address, so a redirect cannot reach a private host."""

    def __init__(self, allow_private=False):
        self.allow_private = allow_private

    def redirect_request(self, request, fp, code, message, headers, newurl):
        check_url(newurl, self.allow_private)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def fetch(url, allow_private=False, timeout=20):
    check_url(url, allow_private)
    request = Request(url, headers={"User-Agent": "agent-team/1.0"})
    try:
        with build_opener(CheckedRedirects(allow_private)).open(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            charset = response.headers.get_content_charset() or "utf-8"
            data = response.read(MAX_FETCH + 1)
    except (URLError, TimeoutError, OSError) as error:
        raise ToolError(f"could not fetch the page: {error}") from None
    text = data[:MAX_FETCH].decode(charset, errors="replace")
    if "html" in content_type:
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
