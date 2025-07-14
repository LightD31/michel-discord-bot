"""
Version RCON optimisée pour obtenir les statistiques Minecraft
Remplace l'approche SSH/SFTP par des commandes RCON plus rapides
"""

import json
import os
import asyncio
from mcrcon import MCRcon
from src import logutil

logger = logutil.init_logger(os.path.basename(__file__))

def ticks_to_hms(ticks):
    """Convertit les ticks Minecraft en format heures:minutes:secondes"""
    seconds = ticks // 20  # Convert ticks to seconds
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def format_time_from_ticks(ticks):
    """Convertit les ticks en secondes pour les calculs"""
    return ticks / 20

class MinecraftRCON:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
    
    async def execute_command(self, command):
        """Exécute une commande RCON de manière asynchrone"""
        try:
            # RCON n'est pas nativement async, on utilise run_in_executor
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, 
                self._execute_sync_command, 
                command
            )
        except Exception as e:
            logger.error(f"Erreur RCON pour la commande '{command}': {e}")
            return None
    
    def _execute_sync_command(self, command):
        """Exécute une commande RCON de manière synchrone"""
        with MCRcon(self.host, self.password, port=self.port) as mcr:
            return mcr.command(command)

async def get_online_players_rcon(rcon_client):
    """Récupère la liste des joueurs en ligne via RCON"""
    try:
        response = await rcon_client.execute_command("list")
        if response:
            # Format: "There are X of a max of Y players online: player1, player2"
            if "online:" in response:
                players_part = response.split("online:")[1].strip()
                if players_part:
                    return [name.strip() for name in players_part.split(",")]
            return []
        return []
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des joueurs en ligne: {e}")
        return []

async def get_player_stats_rcon(rcon_client, player_name):
    """Récupère les statistiques d'un joueur via RCON"""
    try:
        stats = {}
        
        # Commandes pour récupérer les statistiques
        commands = {
            "deaths": f"scoreboard players get {player_name} minecraft.custom:minecraft.deaths",
            "playtime": f"scoreboard players get {player_name} minecraft.custom:minecraft.play_time", 
            "walked": f"scoreboard players get {player_name} minecraft.custom:minecraft.walk_one_cm",
            "level": f"data get entity {player_name} XpLevel"
        }
        
        for stat_name, command in commands.items():
            try:
                response = await rcon_client.execute_command(command)
                if response and "has no score" not in response.lower():
                    # Extraire la valeur numérique de la réponse
                    if stat_name == "level":
                        # Format: "PlayerName has the following entity data: 42"
                        if "entity data:" in response:
                            value = int(response.split("entity data:")[1].strip())
                        else:
                            value = 0
                    else:
                        # Format: "PlayerName has X [objective]"
                        parts = response.split(" has ")
                        if len(parts) > 1:
                            value = int(parts[1].split()[0])
                        else:
                            value = 0
                    stats[stat_name] = value
                else:
                    stats[stat_name] = 0
            except (ValueError, IndexError) as e:
                logger.debug(f"Erreur parsing {stat_name} pour {player_name}: {e}")
                stats[stat_name] = 0
        
        # Calculs dérivés
        playtime_seconds = format_time_from_ticks(stats.get("playtime", 0))
        walked_km = stats.get("walked", 0) / 100000
        deaths_per_hour = stats.get("deaths", 0) / max(1, playtime_seconds / 3600)
        
        return {
            "Joueur": player_name,
            "Niveau": stats.get("level", 0),
            "Morts": stats.get("deaths", 0),
            "Morts/h": round(deaths_per_hour, 2),
            "Marche (km)": round(walked_km, 2),
            "Temps de jeu": playtime_seconds,
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des stats pour {player_name}: {e}")
        return {
            "Joueur": player_name,
            "Niveau": 0,
            "Morts": 0,
            "Morts/h": 0,
            "Marche (km)": 0,
            "Temps de jeu": 0,
        }

async def get_all_player_stats_rcon(rcon_host, rcon_port, rcon_password):
    """Récupère les statistiques de tous les joueurs via RCON"""
    rcon_client = MinecraftRCON(rcon_host, rcon_port, rcon_password)
    
    try:
        # Récupérer la liste des joueurs en ligne
        online_players = await get_online_players_rcon(rcon_client)
        logger.info(f"Joueurs en ligne trouvés: {online_players}")
        
        if not online_players:
            logger.info("Aucun joueur en ligne")
            return []
        
        # Récupérer les stats pour chaque joueur en parallèle
        tasks = [
            get_player_stats_rcon(rcon_client, player) 
            for player in online_players
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filtrer les erreurs
        valid_results = [
            result for result in results 
            if not isinstance(result, Exception)
        ]
        
        return valid_results
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des stats via RCON: {e}")
        return []

async def get_server_info_rcon(rcon_host, rcon_port, rcon_password):
    """Récupère des informations sur le serveur via RCON"""
    rcon_client = MinecraftRCON(rcon_host, rcon_port, rcon_password)
    
    try:
        info = {}
        
        # TPS (Ticks Per Second) - spécifique à Forge
        tps_response = await rcon_client.execute_command("forge tps")
        if tps_response:
            info["tps"] = tps_response
        
        # Chunks chargés - spécifique à Forge  
        chunks_response = await rcon_client.execute_command("forge chunks")
        if chunks_response:
            info["chunks"] = chunks_response
        
        # Temps de jeu du serveur
        time_response = await rcon_client.execute_command("time query gametime")
        if time_response:
            info["server_time"] = time_response
            
        return info
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des infos serveur: {e}")
        return {}

# Fonction de compatibilité pour remplacer l'ancienne approche
async def get_users(sftp_unused, file_unused):
    """Fonction de compatibilité - non utilisée avec RCON"""
    logger.warning("get_users appelée mais RCON ne nécessite pas cette fonction")
    return []

async def get_player_stats(sftp_unused, file_unused, nbtfile_unused):
    """Fonction de compatibilité - non utilisée avec RCON"""
    logger.warning("get_player_stats appelée mais RCON ne nécessite pas cette fonction") 
    return {
        "Joueur": "unknown",
        "Niveau": 0,
        "Morts": 0,
        "Morts/h": 0,
        "Marche (km)": 0,
        "Temps de jeu": 0,
    }
