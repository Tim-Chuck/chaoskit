import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
PRIVATE_NETWORKS = tuple(map(ipaddress.ip_network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
)))


def parsed_origin(value):
    parts = urlsplit(value)
    if parts.scheme != "http" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use a plain HTTP origin without credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("Use an origin without a path, query or fragment")
    return parts


def pin_upstream(value, allowed_hosts):
    parts = parsed_origin(value)
    host = parts.hostname
    if host not in LOOPBACK_HOSTS and host not in allowed_hosts and not host.endswith(".local"):
        raise ValueError("Upstream host is outside the allowlist")
    if host == "localhost":
        host = "127.0.0.1"
    addresses = [ipaddress.ip_address(entry[4][0]) for entry in socket.getaddrinfo(
        host, parts.port or 80, type=socket.SOCK_STREAM,
    )]
    for address in addresses:
        if not address.is_loopback and not any(address in net for net in PRIVATE_NETWORKS):
            raise ValueError("Every upstream address must be loopback or private")
    address = addresses[0]
    target = httpx.URL(value).copy_with(host=str(address))
    return target, parts.netloc


def hop_headers(raw, extra=()):
    ignored = {
        b"connection", b"keep-alive", b"proxy-connection", b"proxy-authorization",
        b"proxy-authenticate", b"te", b"trailer", b"transfer-encoding", b"upgrade", *extra,
    }
    for name, value in raw:
        if name.lower() == b"connection":
            ignored.update(chunk.strip().lower() for chunk in value.split(b","))
    return [(name.lower(), value) for name, value in raw if name.lower() not in ignored]


def incoming_local(value):
    try:
        return parsed_origin("http://" + value).hostname in LOOPBACK_HOSTS
    except ValueError:
        return False
