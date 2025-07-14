# Configuration des optimisations Minecraft
MINECRAFT_OPTIMIZATION_CONFIG = {
    # Cache
    "cache_duration": 300,  # 5 minutes
    "max_image_cache_size": 5,
    
    # SFTP Connection
    "connection_timeout": 30,
    "max_retries": 3,
    "keepalive_interval": 15,
    
    # Performance
    "max_concurrent_connections": 5,
    "max_players_displayed": 15,
    "max_players_processed": 20,
    
    # Formatting
    "player_name_max_length": 12,
    "decimal_precision": 1,
}

# Fonction pour valider et appliquer la configuration
def apply_optimization_config():
    """Applique la configuration d'optimisation"""
    return MINECRAFT_OPTIMIZATION_CONFIG

def get_config(key, default=None):
    """Récupère une valeur de configuration"""
    return MINECRAFT_OPTIMIZATION_CONFIG.get(key, default)
