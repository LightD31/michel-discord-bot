"""
Configuration manager with support for multiple files and cleaner structure.
"""

import json
import os
import glob
from typing import Tuple, Optional
from src import logutil

logger = logutil.init_logger(os.path.basename(__file__))

class ConfigManager:
    """
    Advanced configuration manager supporting multiple files and includes.
    """
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = config_dir
        self.cache = {}
        
    def load_config_file(self, file_path: str) -> dict:
        """Load a single JSON configuration file."""
        try:
            full_path = os.path.join(self.config_dir, file_path)
            with open(full_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            logger.warning(f"Configuration file not found: {file_path}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {file_path}: {e}")
            return {}
    
    def load_multiple_configs(self, pattern: str) -> dict:
        """Load multiple configuration files matching a pattern."""
        configs = {}
        pattern_path = os.path.join(self.config_dir, pattern)
        
        for file_path in glob.glob(pattern_path):
            relative_path = os.path.relpath(file_path, self.config_dir)
            config_data = self.load_config_file(relative_path)
            configs.update(config_data)
            
        return configs
    
    def merge_configs(self, base_config: dict, *additional_configs: dict) -> dict:
        """Merge multiple configuration dictionaries."""
        result = base_config.copy()
        
        for config in additional_configs:
            for key, value in config.items():
                if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = self.merge_configs(result[key], value)
                else:
                    result[key] = value
                    
        return result
    
    def load_full_config(self) -> dict:
        """Load the complete configuration from multiple files."""
        # Load main configuration
        main_config = self.load_config_file("main.json")
        
        # If main.json doesn't exist, fallback to config.json
        if not main_config:
            main_config = self.load_config_file("config.json")
            return main_config
        
        # Load included configurations
        includes = main_config.get("include", {})
        
        # Load services
        services_config = {}
        for service_file in includes.get("services", []):
            service_config = self.load_config_file(service_file)
            services_config = self.merge_configs(services_config, service_config)
        
        # Load servers
        servers_config = {}
        for server_pattern in includes.get("servers", []):
            server_configs = self.load_multiple_configs(server_pattern)
            servers_config = self.merge_configs(servers_config, server_configs)
        
        # Merge everything
        full_config = {
            "config": self.merge_configs(
                main_config.get("config", {}),
                services_config
            ),
            "servers": servers_config
        }
        
        return full_config

# Backward compatibility functions
_config_manager = ConfigManager()

def load_config(module_name: str | None = None) -> Tuple[dict, dict, list[str]]:
    """
    Load the configuration for a specific module (backward compatible).
    """
    data = _config_manager.load_full_config()
    
    if module_name is None:
        return data.get("config", {}), {}, []
        
    enabled_servers = [
        str(server_id)
        for server_id, server_info in data["servers"].items()
        if server_info.get(module_name, {}).get("enabled", False)
    ]
    
    module_config = {
        server_id: server_info.get(module_name, {})
        for server_id, server_info in data["servers"].items()
        if str(server_id) in enabled_servers
    }
    
    config = data.get("config", {})
    logger.info(
        "Loaded config for module %s for servers %s",
        module_name,
        enabled_servers,
    )
    
    return config, module_config, enabled_servers

def save_server_config(server_id: str, server_config: dict):
    """Save configuration for a specific server."""
    server_file = f"config/servers/{server_id}.json"
    os.makedirs(os.path.dirname(server_file), exist_ok=True)
    
    with open(server_file, "w", encoding="utf-8") as file:
        json.dump({server_id: server_config}, file, indent=4, ensure_ascii=False)
    
    logger.info(f"Saved configuration for server {server_id}")
