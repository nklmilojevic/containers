"""The upstream resolver and the loop guard (`http/dns.py`)."""
import asyncio
import socket
import struct

import pytest

from petkit_local.http import dns
from petkit_local.http.dns import (
    UpstreamResolver,
    _build_query,
    _parse_answers,
    _skip_name,
    forget_cache,
    loops_back,
    query_a,
    resolve_a,
)


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    # The real 3s wait is for a device on a slow network, not for a test that
    # answers over loopback or not at all.
    monkeypatch.setattr(dns, "DNS_TIMEOUT", 0.2)
    forget_cache()
    yield
    forget_cache()


def _answer(txid, name="api-eu.petkt.com", addresses=("3.66.36.97",), *,
            rcode=0, compressed=True, extra_rrs=()):
    """A DNS response for `name`, built the way a real server would."""
    header = struct.pack("!HHHHHH", txid, 0x8180 | rcode, 1,
                         len(addresses) + len(extra_rrs), 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    question = qname + struct.pack("!HH", 1, 1)

    body = b""
    for rtype, rdata in extra_rrs:
        pointer = b"\xc0\x0c" if compressed else qname
        body += pointer + struct.pack("!HHIH", rtype, 1, 300, len(rdata)) + rdata
    for address in addresses:
        pointer = b"\xc0\x0c" if compressed else qname
        body += pointer + struct.pack("!HHIH", 1, 1, 300, 4) + socket.inet_aton(address)
    return header + question + body


def test_a_query_is_a_recursive_a_lookup():
    q = _build_query("api-eu.petkt.com", 0x1234)
    txid, flags, qdcount = struct.unpack_from("!HHH", q, 0)
    assert txid == 0x1234
    assert flags == 0x0100          # RD set, nothing else
    assert qdcount == 1
    assert q.endswith(struct.pack("!HH", 1, 1))   # QTYPE=A, QCLASS=IN
    assert b"\x06api-eu\x05petkt\x03com\x00" in q


def test_answers_are_read_out_of_a_real_shaped_reply():
    assert _parse_answers(_answer(7, addresses=("3.66.36.97", "3.77.161.44")), 7) == [
        "3.66.36.97", "3.77.161.44"]


def test_a_cname_in_front_of_the_addresses_is_stepped_over():
    """PetKit's EU name is a CNAME to an AWS load balancer, so the A records
    arrive behind one. Reading the first answer blindly would return the CNAME's
    bytes as if they were an address."""
    cname = b"\x03elb\x03aws\x00"
    packet = _answer(9, addresses=("3.66.36.97",), extra_rrs=((5, cname),))
    assert _parse_answers(packet, 9) == ["3.66.36.97"]


def test_a_reply_with_the_wrong_transaction_id_is_refused():
    """A UDP socket accepts whatever arrives; the id is what makes an off-path
    forgery need luck rather than just timing."""
    with pytest.raises(ValueError, match="transaction id"):
        _parse_answers(_answer(1), 2)


def test_an_error_rcode_is_not_read_as_an_empty_answer():
    with pytest.raises(ValueError, match="rcode"):
        _parse_answers(_answer(1, addresses=(), rcode=3), 1)   # NXDOMAIN


def test_a_compression_pointer_ends_a_name_rather_than_being_followed():
    """A pointer that points at itself is a classic decompression bomb. Names
    are only skipped here, never read, so the loop cannot be entered."""
    assert _skip_name(b"\xc0\x00rest", 0) == 2
    with pytest.raises(ValueError, match="truncated"):
        _skip_name(b"\x05part", 0)


async def _fake_dns(responder):
    """A UDP server that answers with `responder(query_bytes)`. Returns 'ip:port'."""
    loop = asyncio.get_running_loop()

    class Server(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            reply = responder(data)
            if reply is not None:
                self.transport.sendto(reply, addr)

    transport, protocol = await loop.create_datagram_endpoint(
        Server, local_addr=("127.0.0.1", 0))
    host, port = transport.get_extra_info("sockname")[:2]
    return transport, f"{host}:{port}"


async def test_a_query_goes_to_the_named_server_and_comes_back():
    def responder(query):
        txid = struct.unpack_from("!H", query, 0)[0]
        return _answer(txid, addresses=("203.0.113.9",))

    transport, server = await _fake_dns(responder)
    try:
        assert await query_a("api-eu.petkt.com", server) == ["203.0.113.9"]
    finally:
        transport.close()


async def test_a_dead_resolver_yields_no_answer_instead_of_raising():
    """DNS failing must not become a second, separate way for proxy mode to
    break — the connection attempt is what should report the problem."""
    transport, server = await _fake_dns(lambda q: None)
    try:
        assert await resolve_a("api-eu.petkt.com", server) == []
    finally:
        transport.close()


async def test_an_answer_is_cached_so_the_loop_check_is_not_a_query_per_request():
    queries = []

    def responder(query):
        queries.append(query)
        return _answer(struct.unpack_from("!H", query, 0)[0])

    transport, server = await _fake_dns(responder)
    try:
        for _ in range(3):
            assert await resolve_a("api-eu.petkt.com", server) == ["3.66.36.97"]
        assert len(queries) == 1
    finally:
        transport.close()


async def test_an_ip_literal_needs_no_resolver_at_all():
    assert await resolve_a("192.0.2.7", "203.0.113.1:9") == ["192.0.2.7"]


# --- the loop guard ---------------------------------------------------------

async def _dns_returning(address):
    return await _fake_dns(
        lambda q: _answer(struct.unpack_from("!H", q, 0)[0], addresses=(address,)))


async def test_an_upstream_that_resolves_to_us_on_our_own_port_is_a_loop():
    """The failure this exists for: forwarding into ourselves does not error,
    it answers — and the answer is then recorded as the cloud's."""
    transport, server = await _dns_returning("192.0.2.20")
    try:
        assert await loops_back("http://api.eu-pet.com", ("192.0.2.20", 80),
                                server) == "192.0.2.20:80"
    finally:
        transport.close()


async def test_the_same_address_on_a_different_port_is_not_a_loop():
    """443 on our address is the MQTT TLS listener, not the device API.
    Forwarding there fails loudly, which is not the case worth blocking — and
    treating it as a loop would stop a legitimate upstream on a shared host."""
    transport, server = await _dns_returning("192.0.2.20")
    try:
        assert await loops_back("https://api-eu.petkt.com", ("192.0.2.20", 80),
                                server) == ""
    finally:
        transport.close()


async def test_a_normal_upstream_is_not_flagged():
    transport, server = await _dns_returning("3.66.36.97")
    try:
        assert await loops_back("http://api.eu-pet.com", ("192.0.2.20", 80),
                                server) == ""
    finally:
        transport.close()


async def test_the_guard_says_nothing_when_it_cannot_tell():
    """No local socket, or a resolver that will not answer, must not be reported
    as a loop — refusing to forward on a guess is its own outage."""
    assert await loops_back("http://api.eu-pet.com", None) == ""
    assert await loops_back("", ("192.0.2.20", 80)) == ""


async def test_the_resolver_reports_a_failure_as_a_connection_error():
    """aiohttp turns OSError from a resolver into a ClientConnectorError, which
    `forward` already catches and falls back from. Returning [] instead would
    look like a host with no addresses and raise something less obvious."""
    transport, server = await _fake_dns(lambda q: None)
    try:
        with pytest.raises(OSError, match="could not be resolved"):
            await UpstreamResolver(server).resolve("api-eu.petkt.com", 443)
    finally:
        transport.close()


async def test_the_resolver_hands_aiohttp_the_shape_it_expects():
    transport, server = await _dns_returning("3.66.36.97")
    try:
        results = await UpstreamResolver(server).resolve("api-eu.petkt.com", 443)
        assert results == [{
            "hostname": "api-eu.petkt.com",
            "host": "3.66.36.97",
            "port": 443,
            "family": socket.AF_INET,
            "proto": 0,
            "flags": socket.AI_NUMERICHOST,
        }]
    finally:
        transport.close()
