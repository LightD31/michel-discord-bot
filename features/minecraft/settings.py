"""A guild's Minecraft settings (``moduleMinecraft``), read from its live config."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _port(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class MinecraftSettings:
    """What the status and stats tasks need from one guild's module config."""

    address: str  # public name shown to members and looked up first
    ip: str
    port: int
    server_type: str = ""
    modpack_name: str = ""
    modpack_url: str = ""
    modpack_version: str = ""
    status_url: str = ""
    footer_text: str = ""
    sftp_host: str = ""
    sftp_port: int = 2225
    sftp_username: str = "Discord"
    sftp_password: str = ""
    sftp_host_key: str = ""

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> MinecraftSettings:
        ip = _text(config.get("minecraftIp"))
        return cls(
            address=_text(config.get("minecraftUrl")),
            ip=ip,
            port=_port(config.get("minecraftPort"), 0),
            server_type=_text(config.get("minecraftServerType")),
            modpack_name=_text(config.get("minecraftModpackName")),
            modpack_url=_text(config.get("minecraftModpackUrl")),
            modpack_version=_text(config.get("minecraftModpackVersion")),
            status_url=_text(config.get("minecraftStatusUrl")),
            footer_text=_text(config.get("minecraftFooterText")),
            sftp_host=_text(config.get("minecraftSftpHost")) or ip,
            sftp_port=_port(config.get("minecraftSftpPort"), 2225),
            sftp_username=_text(config.get("minecraftSftpUsername")) or "Discord",
            sftp_password=_text(config.get("minecraftSftpsPassword")),
            sftp_host_key=_text(config.get("minecraftSftpHostKey")),
        )

    @property
    def has_server(self) -> bool:
        """Whether there is a server to query at all."""
        return bool(self.address or self.ip)


__all__ = ["MinecraftSettings"]
