# backend/safety.py — public-address validation and a DNS-pinning browser egress proxy.
import asyncio
import ipaddress
import socket
from urllib.parse import unquote, urlsplit


def validate_url(url: str) -> str:
    # Reject credentials, ambiguous authorities, unsafe schemes, and write-oriented paths.
    if not url or len(url) > 2048 or any(ord(char) < 33 or char in '<>"`\\' for char in url):
        raise ValueError("Malformed or oversized URL")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Only public HTTP(S) URLs without credentials are permitted")
    if parts.port not in {None, 80, 443}:
        raise ValueError("Only standard web ports are permitted")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home", ".lan")) or "." not in host and ":" not in host:
        raise ValueError("Local and internal hosts are blocked")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not public_address(address):
        raise ValueError("Non-public IP address blocked")
    segments = unquote(parts.path + "?" + parts.query).lower()
    # This defense supplements GET-only routing and the absence of writable browser tools.
    forbidden = ("logout", "signout", "sign-out", "delete", "checkout", "payment", "unsubscribe", "wp-admin", "account/settings", "action=", "do=", "cmd=")
    if any(word in segments for word in forbidden):
        raise ValueError("Potential account-changing or destructive URL blocked")
    return url


def public_address(address) -> bool:
    # Deny private, loopback, multicast, reserved, and IPv4-in-IPv6 bypasses.
    return address.is_global and not address.is_multicast and not address.is_reserved and not getattr(address, "ipv4_mapped", None) and not getattr(address, "sixtofour", None) and not getattr(address, "teredo", None)


async def public_endpoint(host: str, port: int) -> str:
    # Resolve once, validate every answer, then connect to that exact numeric IP.
    records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 8)
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not public_address(ipaddress.ip_address(address)) for address in addresses):
        raise ValueError("DNS resolved to a non-public address")
    return addresses[0]


class EgressProxy:
    """Pin DNS at connection time so Chromium cannot re-resolve a validated host privately."""

    def __init__(self):
        # Track connections for deterministic cleanup when research ends.
        self.server = None
        self.connections = set()
        self.handlers = set()

    async def start(self):
        # The proxy is ephemeral and bound only to the loopback interface.
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0, limit=65536)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def pipe(self, reader, writer):
        # Relay bounded chunks; idle sockets cannot outlive a short timeout.
        while chunk := await asyncio.wait_for(reader.read(65536), 30):
            writer.write(chunk)
            await writer.drain()

    async def handle(self, reader, writer):
        # Support HTTP GET and HTTPS CONNECT while preventing access to internal services.
        task = asyncio.current_task()
        self.handlers.add(task)
        self.connections.add(writer)
        upstream = None
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = header.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            if method == "CONNECT":
                parts = urlsplit("https://" + target)
                validate_url("https://" + target)
                if parts.port != 443:
                    raise ValueError("CONNECT requires port 443")
                port = 443
            else:
                if method not in {"GET", "HEAD"}:
                    raise ValueError("Only read-only HTTP requests are supported")
                validate_url(target)
                parts = urlsplit(target)
                if parts.scheme != "http":
                    raise ValueError("HTTPS requires CONNECT")
                port = parts.port or 80
            address = await public_endpoint(parts.hostname, port)
            upstream_reader, upstream = await asyncio.wait_for(asyncio.open_connection(address, port), 10)
            self.connections.add(upstream)
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
            else:
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                forwarded = [line for line in lines[1:] if line and line.split(":", 1)[0].lower() not in {"connection", "proxy-connection", "proxy-authorization", "host", "content-length", "transfer-encoding"}]
                request = f"{method} {path} HTTP/1.1\r\nHost: {parts.netloc}\r\n" + "\r\n".join(forwarded) + "\r\nConnection: close\r\n\r\n"
                upstream.write(request.encode("latin-1"))
                await upstream.drain()
            tasks = [asyncio.create_task(self.pipe(reader, upstream)), asyncio.create_task(self.pipe(upstream_reader, writer))]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for relay in tasks:
                    relay.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (ValueError, OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            try:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            except (OSError, RuntimeError):
                pass
        finally:
            for stream in (writer, upstream):
                if stream:
                    stream.close()
                    self.connections.discard(stream)
            self.handlers.discard(task)

    async def close(self):
        # Stop accepting connections, close tunnels, and reap their relay tasks.
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for stream in list(self.connections):
            stream.close()
        tasks = list(self.handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
