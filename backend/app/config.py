import os
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field
import yaml


BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = BASE_DIR / "config"
LOGS_DIR = BASE_DIR / "logs"
TEMPLATES_DIR = BASE_DIR / "templates"

CONFIG_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)


class ServerConfig(BaseModel):
    id: str
    name: str
    host: str
    port: int = 22
    username: str
    password: Optional[str] = ""
    private_key: Optional[str] = ""
    tags: List[str] = Field(default_factory=list)

    @property
    def key_file(self) -> Optional[str]:
        if self.private_key:
            expanded = os.path.expanduser(self.private_key)
            return expanded if os.path.exists(expanded) else None
        return None


class AppSettings(BaseModel):
    servers: List[ServerConfig] = Field(default_factory=list)
    ssh_pool_size: int = 10
    ssh_timeout: int = 30
    max_concurrent_tasks: int = 20
    log_retention_days: int = 30

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "AppSettings":
        path = Path(config_path) if config_path else CONFIG_DIR / "servers.yaml"
        if not path.exists():
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        servers_data = data.get("servers", [])
        return cls(
            servers=[ServerConfig(**s) for s in servers_data],
            ssh_pool_size=data.get("ssh_pool_size", 10),
            ssh_timeout=data.get("ssh_timeout", 30),
            max_concurrent_tasks=data.get("max_concurrent_tasks", 20),
            log_retention_days=data.get("log_retention_days", 30),
        )

    def save(self, config_path: Optional[str] = None) -> None:
        path = Path(config_path) if config_path else CONFIG_DIR / "servers.yaml"
        data = {
            "servers": [s.model_dump() for s in self.servers],
            "ssh_pool_size": self.ssh_pool_size,
            "ssh_timeout": self.ssh_timeout,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "log_retention_days": self.log_retention_days,
        }
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    def get_server(self, server_id: str) -> Optional[ServerConfig]:
        for s in self.servers:
            if s.id == server_id:
                return s
        return None

    def add_server(self, server: ServerConfig) -> None:
        existing = self.get_server(server.id)
        if existing:
            self.servers = [s if s.id != server.id else server for s in self.servers]
        else:
            self.servers.append(server)
        self.save()

    def remove_server(self, server_id: str) -> bool:
        original_len = len(self.servers)
        self.servers = [s for s in self.servers if s.id != server_id]
        if len(self.servers) != original_len:
            self.save()
            return True
        return False


settings = AppSettings.load()
