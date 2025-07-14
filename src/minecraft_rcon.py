"""
Version RCON optimisée pour obtenir les statistiques Minecraft
Remplace l'approche SSH/SFTP par des commandes RCON plus rapides
"""

import json
import os
import asyncio
import socket
import struct
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
    """Implémentation RCON asynchrone native pour éviter les problèmes de threading"""
    
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.socket = None
        self.request_id = 0
    
    async def connect(self):
        """Établit la connexion RCON"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(10)
            await asyncio.get_event_loop().run_in_executor(
                None, self.socket.connect, (self.host, self.port)
            )
            
            # Authentification
            await self._send_packet(3, self.password)  # Type 3 = LOGIN
            response = await self._receive_packet()
            
            if response[0] == -1:  # ID -1 indique un échec d'authentification
                raise Exception("Échec de l'authentification RCON")
                
            return True
        except Exception as e:
            logger.error(f"Erreur de connexion RCON: {e}")
            if self.socket:
                self.socket.close()
                self.socket = None
            return False
    
    async def disconnect(self):
        """Ferme la connexion RCON"""
        if self.socket:
            self.socket.close()
            self.socket = None
    
    async def execute_command(self, command):
        """Exécute une commande RCON"""
        if not self.socket:
            if not await self.connect():
                return None
        
        try:
            await self._send_packet(2, command)  # Type 2 = COMMAND
            response = await self._receive_packet()
            return response[2]  # Retourner la réponse
        except Exception as e:
            logger.error(f"Erreur RCON pour la commande '{command}': {e}")
            await self.disconnect()
            return None
    
    async def _send_packet(self, packet_type, data):
        """Envoie un paquet RCON"""
        self.request_id += 1
        packet_id = self.request_id
        
        # Structure du paquet RCON
        data_bytes = data.encode('utf-8')
        packet_size = 4 + 4 + len(data_bytes) + 2  # ID + Type + Data + 2 null bytes
        
        packet = struct.pack('<i', packet_size)  # Taille du paquet
        packet += struct.pack('<i', packet_id)   # ID de la requête
        packet += struct.pack('<i', packet_type) # Type de paquet
        packet += data_bytes                     # Données
        packet += b'\x00\x00'                   # Null terminators
        
        await asyncio.get_event_loop().run_in_executor(
            None, self.socket.send, packet
        )
        
        return packet_id
    
    async def _receive_packet(self):
        """Reçoit un paquet RCON"""
        # Lire la taille du paquet
        size_data = await asyncio.get_event_loop().run_in_executor(
            None, self.socket.recv, 4
        )
        if len(size_data) < 4:
            raise Exception("Paquet RCON incomplet")
        
        packet_size = struct.unpack('<i', size_data)[0]
        
        # Lire le reste du paquet
        remaining_data = await asyncio.get_event_loop().run_in_executor(
            None, self.socket.recv, packet_size
        )
        
        if len(remaining_data) < packet_size:
            raise Exception("Données RCON incomplètes")
        
        # Décoder le paquet
        request_id = struct.unpack('<i', remaining_data[0:4])[0]
        packet_type = struct.unpack('<i', remaining_data[4:8])[0]
        data = remaining_data[8:-2].decode('utf-8')  # Exclure les null terminators
        
        return (request_id, packet_type, data)

async def get_online_players_rcon(rcon_client):
    """Récupère la liste des joueurs en ligne via RCON"""
    try:
        response = await rcon_client.execute_command("list")
        if response:
            # Format: "There are X of a max of Y players online: player1, player2"
            logger.debug(f"Réponse list: {response}")
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
            "deaths": f"data get entity {player_name} Stats.\"minecraft:custom\".\"minecraft:deaths\"",
            "playtime": f"data get entity {player_name} Stats.\"minecraft:custom\".\"minecraft:play_time\"", 
            "walked": f"data get entity {player_name} Stats.\"minecraft:custom\".\"minecraft:walk_one_cm\"",
            "level": f"data get entity {player_name} XpLevel"
        }
        
        for stat_name, command in commands.items():
            try:
                response = await rcon_client.execute_command(command)
                logger.debug(f"Réponse {stat_name} pour {player_name}: {response}")
                
                if response and "No entity was found" not in response:
                    # Extraire la valeur numérique de la réponse
                    if "has the following entity data:" in response:
                        value_str = response.split("has the following entity data:")[1].strip()
                        # Enlever les suffixes comme 'd', 'f', 'L' etc.
                        value_str = value_str.rstrip('dflLbsif')
                        try:
                            value = float(value_str)
                            stats[stat_name] = int(value) if value.is_integer() else value
                        except ValueError:
                            stats[stat_name] = 0
                    else:
                        stats[stat_name] = 0
                else:
                    stats[stat_name] = 0
            except Exception as e:
                logger.debug(f"Erreur parsing {stat_name} pour {player_name}: {e}")
                stats[stat_name] = 0
        
        # Calculs dérivés
        playtime_seconds = format_time_from_ticks(stats.get("playtime", 0))
        walked_km = stats.get("walked", 0) / 100000
        deaths_per_hour = stats.get("deaths", 0) / max(1, playtime_seconds / 3600)
        
        return {
            "Joueur": player_name,
            "Niveau": int(stats.get("level", 0)),
            "Morts": int(stats.get("deaths", 0)),
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
        # Établir la connexion
        if not await rcon_client.connect():
            logger.error("Impossible de se connecter au serveur RCON")
            return []
        
        # Récupérer la liste des joueurs en ligne
        online_players = await get_online_players_rcon(rcon_client)
        logger.info(f"Joueurs en ligne trouvés: {online_players}")
        
        if not online_players:
            logger.info("Aucun joueur en ligne")
            return []
        
        # Récupérer les stats pour chaque joueur
        results = []
        for player in online_players:
            stats = await get_player_stats_rcon(rcon_client, player)
            if stats:
                results.append(stats)
        
        return results
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des stats via RCON: {e}")
        return []
    finally:
        await rcon_client.disconnect()

async def get_server_info_rcon(rcon_host, rcon_port, rcon_password):
    """Récupère des informations sur le serveur via RCON"""
    rcon_client = MinecraftRCON(rcon_host, rcon_port, rcon_password)
    
    try:
        if not await rcon_client.connect():
            return {}
            
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
    finally:
        await rcon_client.disconnect()

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
