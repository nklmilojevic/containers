"""Resolving PetKit's names when the LAN has been told they are us.

Redirecting `api-eu.petkt.com` at a Pi-hole or router is one of the two ways to
point a device here, and it is the one that costs nothing to try. It also breaks
proxy mode, because proxy mode resolves that same name through that same DNS and
gets **this add-on** back. The forwarded request re-enters `/6/`,
`proxy_middleware` forwards it again, and so on.

That failure is worse than it sounds. Forwarding into ourselves does not error —
our own handler answers, plausibly, and the reply is recorded as "the cloud's",
so the panel shows proxy mode working while the device has never once reached
PetKit. Detecting it is therefore not a nicety: an undetected loop is
indistinguishable from success. `loops_back` is the check, and it compares the
upstream's address against the address the device reached *us* on, which is the
one thing we know for certain is ours.

The fix is `proxy_dns`: a resolver to ask instead of the system one. Empty by
default, because a DNS server is somewhere queries go, and this add-on does not
send them anywhere its owner did not name.

No new dependency. `aiohttp.resolver.AsyncResolver` would need `aiodns`, hence
`pycares`, a C extension — the musl/armv7 wheel problem this project has already
had once. A query for one A record is small enough to write out.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import socket
import struct
import time
from urllib.parse import urlparse

from aiohttp.abc import AbstractResolver

log = logging.getLogger(__name__)

#: Default DNS port, and how long to wait. Short: a device heartbeats every ~15s
#: and `UPSTREAM_TIMEOUT` is 8s for the whole exchange, so a resolver that has to
#: be waited out has already cost more than the answer is worth.
DNS_PORT = 53
DNS_TIMEOUT = 3.0

#: How long a resolved address is reused. aiohttp's own connector cache covers
#: the forwarding path; this one exists for `loops_back`, which would otherwise
#: query on every single device request.
CACHE_TTL = 60.0

_QTYPE_A = 1
_QCLASS_IN = 1

_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}


def _build_query(name: str, txid: int) -> bytes:
    """One standard recursive A query for `name`."""
    header = struct.pack("!HHHHHH", txid, 0x0100, 1, 0, 0, 0)  # 0x0100 = RD
    labels = b"".join(
        bytes([len(part)]) + part
        for part in (label.encode("idna") for label in name.rstrip(".").split("."))
    )
    return header + labels + b"\x00" + struct.pack("!HH", _QTYPE_A, _QCLASS_IN)


def _skip_name(data: bytes, offset: int) -> int:
    """Return the offset just past the name at `offset`.

    Names are length-prefixed labels, except that a label whose top two bits are
    set is a 16-bit pointer into the packet and ends the name. Only the length
    matters here — the name itself is never read back — so a pointer is stepped
    over rather than followed, which also makes a pointer loop impossible.
    """
    while offset < len(data):
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += 1 + length
    raise ValueError("truncated name")


def _parse_answers(data: bytes, txid: int) -> list[str]:
    """Every A record in `data`, as dotted-quad strings.

    CNAMEs need no special handling: the server that followed the chain returns
    the A records alongside it, and anything that is not `_QTYPE_A` with a
    4-byte payload is skipped.
    """
    if len(data) < 12:
        raise ValueError("truncated header")
    got_id, flags, qdcount, ancount = struct.unpack_from("!HHHH", data, 0)
    if got_id != txid:
        raise ValueError("transaction id mismatch")
    rcode = flags & 0x0F
    if rcode != 0:
        raise ValueError(f"server returned rcode {rcode}")

    offset = 12
    for _ in range(qdcount):
        offset = _skip_name(data, offset) + 4  # qtype + qclass

    addresses = []
    for _ in range(ancount):
        offset = _skip_name(data, offset)
        rtype, _rclass, _ttl, rdlength = struct.unpack_from("!HHIH", data, offset)
        offset += 10
        if rtype == _QTYPE_A and rdlength == 4:
            addresses.append(socket.inet_ntoa(data[offset:offset + 4]))
        offset += rdlength
    return addresses


class _QueryProtocol(asyncio.DatagramProtocol):
    """Resolves `future` with the first datagram the socket receives.

    One future per query, so the second answer to a retransmitted question is
    dropped rather than raising InvalidStateError on a settled future.
    """

    def __init__(self, future: asyncio.Future) -> None:
        self._future = future

    def datagram_received(self, data: bytes, addr) -> None:
        if not self._future.done():
            self._future.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self._future.done():
            self._future.set_exception(exc)


async def query_a(name: str, server: str) -> list[str]:
    """Ask `server` for `name`'s A records. Raises on anything but an answer."""
    host, _, port = server.partition(":")
    txid = secrets.randbits(16)
    loop = asyncio.get_running_loop()
    answer: asyncio.Future[bytes] = loop.create_future()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _QueryProtocol(answer),
        remote_addr=(host, int(port) if port else DNS_PORT),
    )
    try:
        transport.sendto(_build_query(name, txid))
        data = await asyncio.wait_for(answer, DNS_TIMEOUT)
    finally:
        transport.close()
    return _parse_answers(data, txid)


async def resolve_a(name: str, server: str) -> list[str]:
    """`name`'s addresses via `server`, or via the system resolver if empty.

    Cached for `CACHE_TTL`, and an empty list on failure rather than a raise:
    both callers treat "no answer" as "carry on and let the connection attempt
    report the problem", which keeps a DNS outage from being a second, separate
    way for proxy mode to break.
    """
    if _is_ip_literal(name):
        return [name]

    key = (name, server)
    cached = _cache.get(key)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1]

    try:
        if server:
            addresses = await query_a(name, server)
        else:
            infos = await asyncio.get_running_loop().getaddrinfo(
                name, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
            addresses = [info[4][0] for info in infos]
    except Exception as exc:
        log.debug("DNS: %s via %s failed: %s", name, server or "system", exc)
        return []

    _cache[key] = (now + CACHE_TTL, addresses)
    return addresses


def _is_ip_literal(host: str) -> bool:
    """Whether `host` is already a dotted-quad, so no lookup is needed."""
    try:
        socket.inet_aton(host)
    except OSError:
        return False
    return host.count(".") == 3


def forget_cache() -> None:
    """Drop every cached answer. For tests, and for a settings change."""
    _cache.clear()


async def loops_back(
    upstream: str, our_socket: tuple[str, int] | None, dns_server: str = ""
) -> str:
    """`"address:port"` if `upstream` points back at this server, else `""`.

    Args:
        upstream: A base URL from `proxy.resolve_upstream`.
        our_socket: The local `(address, port)` the device's own connection
            arrived on — `request.transport.get_extra_info("sockname")`. This
            beats enumerating our interfaces: it is not a guess about which
            address is ours, it is the one a device just used.
        dns_server: `proxy_dns`, so the check asks whatever forwarding will.

    The port is part of the comparison, and has to be. Our address hosts several
    unrelated listeners — the MQTT TLS one is on 443, which is the port an HTTPS
    upstream uses — and forwarding into *those* fails loudly and harmlessly. Only
    a match on the port the device API is answering on produces the dangerous
    case this exists for: a reply of our own, returned as the cloud's.
    """
    parsed = urlparse(upstream)
    if not parsed.hostname or not our_socket:
        return ""
    our_address, our_port = our_socket[0], our_socket[1]
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port != our_port:
        return ""
    for address in await resolve_a(parsed.hostname, dns_server):
        if address == our_address:
            return f"{address}:{port}"
    return ""


class UpstreamResolver(AbstractResolver):
    """An `aiohttp` resolver that asks one named DNS server over UDP.

    Installed on the proxy session's connector only, so it changes where PetKit's
    names are looked up and nothing else: the device-facing server, the HA broker
    connection and media fetches all keep using the system resolver.
    """

    def __init__(self, server: str) -> None:
        self._server = server

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[dict]:
        """aiohttp's resolver contract: `host` -> a list of address dicts.

        IPv4 only, because that is all `query_a` asks for.

        Raises:
            OSError: the configured server returned no address. aiohttp treats
                any other exception type as a bug rather than a lookup failure.
        """
        addresses = await resolve_a(host, self._server)
        if not addresses:
            raise OSError(f"{host} could not be resolved via {self._server}")
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": socket.AF_INET,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
            for address in addresses
        ]

    async def close(self) -> None:
        """Nothing to release — each query owns its socket for its lifetime."""
