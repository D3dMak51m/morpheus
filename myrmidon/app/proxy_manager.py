"""
MYRMIDON — Proxy Manager Module
==================================
Manages per-agent SOCKS5 proxy isolation for Appium mobile sessions.

Each AI agent persona has a dedicated residential SOCKS5 proxy whose
geolocation matches the agent's legend (city/country from the YAML config).
This module ensures that every Appium session and Pyrogram client
routes traffic exclusively through the assigned proxy.

From tech_spec.md (section 6.2):
  "В MYRMIDON запуск каждой мобильной сессии Appium настраивается
   с флагом удаленной отладки Chrome и жестко привязывается к
   резидентному соксовому прокси (SOCKS5), геопозиция которого
   совпадает с легендой «души» из файла конфигурации."
"""

import logging
import os
import socket
from typing import Optional
from urllib.parse import urlparse

import socks

logger = logging.getLogger("myrmidon.proxy_manager")


class ProxyConfig:
    """Parsed SOCKS5 proxy configuration."""

    def __init__(self, proxy_url: str) -> None:
        """
        Parse a proxy URL string.

        Supported formats:
          - socks5://user:pass@host:port
          - socks5://host:port
          - host:port (assumes SOCKS5, no auth)
        """
        self.raw = proxy_url
        self.host: str = ""
        self.port: int = 1080
        self.username: Optional[str] = None
        self.password: Optional[str] = None

        self._parse(proxy_url)

    def _parse(self, proxy_url: str) -> None:
        """Parse the proxy URL into components."""
        if "://" not in proxy_url:
            proxy_url = f"socks5://{proxy_url}"

        parsed = urlparse(proxy_url)
        self.host = parsed.hostname or ""
        self.port = parsed.port or 1080
        self.username = parsed.username
        self.password = parsed.password

    def to_appium_caps(self) -> dict:
        """
        Generate Appium desired capabilities for proxy configuration.
        Used when launching an Android emulator session.
        """
        caps = {
            "chromedriverChromeSwitches": [
                f"--proxy-server=socks5://{self.host}:{self.port}",
                "--remote-debugging-port=9222",
            ],
        }
        return caps

    def to_pyrogram_proxy(self) -> dict:
        """
        Generate Pyrogram-compatible proxy configuration dict.
        Used when initializing a Pyrogram MTProto client.
        """
        proxy = {
            "scheme": "socks5",
            "hostname": self.host,
            "port": self.port,
        }
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy

    def __repr__(self) -> str:
        auth = f"{self.username}@" if self.username else ""
        return f"<ProxyConfig({auth}{self.host}:{self.port})>"


# ── Module-level active proxy ────────────────────────────────────────────

_active_proxy: Optional[ProxyConfig] = None


def configure_proxy(proxy_url: str) -> ProxyConfig:
    """
    Set the active proxy for the current execution context.
    Configures the PySocks default socket to route all traffic
    through the specified SOCKS5 proxy.

    Args:
        proxy_url: The SOCKS5 proxy URL (e.g., 'socks5://user:pass@1.2.3.4:1080')

    Returns:
        The parsed ProxyConfig object.
    """
    global _active_proxy

    config = ProxyConfig(proxy_url)

    # Set PySocks as the default socket for all outgoing connections
    socks.set_default_proxy(
        socks.SOCKS5,
        addr=config.host,
        port=config.port,
        username=config.username,
        password=config.password,
    )
    socket.socket = socks.socksocket  # type: ignore[misc]

    _active_proxy = config
    logger.info("Proxy configured: %s", config)
    return config


def get_active_proxy() -> Optional[ProxyConfig]:
    """Return the currently active proxy configuration, or None."""
    return _active_proxy


def reset_proxy() -> None:
    """
    Reset the socket to direct connection (no proxy).
    Call this when switching between agents with different proxies.
    """
    global _active_proxy

    socks.set_default_proxy()
    socket.socket = socks.socksocket  # type: ignore[misc]
    _active_proxy = None
    logger.info("Proxy reset — direct connection restored.")


def test_proxy_connectivity(proxy_url: str, test_host: str = "api.ipify.org", test_port: int = 443) -> bool:
    """
    Test whether a proxy is reachable by attempting a TCP connection
    through it to a well-known endpoint.

    Args:
        proxy_url: The SOCKS5 proxy URL to test.
        test_host: The host to connect to through the proxy.
        test_port: The port to connect to.

    Returns:
        True if the connection succeeded, False otherwise.
    """
    config = ProxyConfig(proxy_url)

    try:
        test_socket = socks.socksocket()
        test_socket.set_proxy(
            socks.SOCKS5,
            addr=config.host,
            port=config.port,
            username=config.username,
            password=config.password,
        )
        test_socket.settimeout(10)
        test_socket.connect((test_host, test_port))
        test_socket.close()
        logger.info("Proxy %s — connectivity test PASSED.", config)
        return True
    except Exception as exc:
        logger.warning("Proxy %s — connectivity test FAILED: %s", config, exc)
        return False
