from io import BytesIO
import json
import asyncio
import asyncssh
import os
import time
from datetime import datetime, timedelta
from src import logutil
import nbtlib
import gzip

logger = logutil.init_logger(os.path.basename(__file__))

class MinecraftStatsCache:
    """Cache pour éviter les lectures répétées des fichiers"""
    def __init__(self, cache_duration=300):  # 5 minutes par défaut
        self.cache = {}
        self.cache_duration = cache_duration
        
    def is_valid(self, key):
        """Vérifie si le cache est encore valide"""
        if key not in self.cache:
            return False
        _, timestamp = self.cache[key]
        return time.time() - timestamp < self.cache_duration
    
    def get(self, key):
        """Récupère une valeur du cache"""
        if self.is_valid(key):
            data, _ = self.cache[key]
            return data
        return None
    
    def set(self, key, value):
        """Stocke une valeur dans le cache"""
        self.cache[key] = (value, time.time())
    
    def clear(self):
        """Vide le cache"""
        self.cache.clear()

# Instance globale du cache
stats_cache = MinecraftStatsCache()

def ticks_to_hms(ticks):
    seconds = ticks // 20  # Convert ticks to seconds
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

async def get_users(sftp: asyncssh.SFTPClient, file):
    """Récupère la liste des utilisateurs avec cache"""
    cache_key = f"users_{file}"
    
    # Vérifier le cache
    cached_data = stats_cache.get(cache_key)
    if cached_data is not None:
        logger.debug(f"Using cached user data for {file}")
        return cached_data
    
    # Lire depuis le serveur
    async with sftp.open(file) as f:
        data = await f.read()
        userdata = json.loads(data)
        
    # Mettre en cache
    stats_cache.set(cache_key, userdata)
    logger.debug(f"Cached user data for {file}")
    return userdata

async def get_all_player_stats_optimized(sftp: asyncssh.SFTPClient):
    """Version optimisée qui traite tous les joueurs en une seule fois"""
    try:
        # 1. Récupérer la liste des utilisateurs
        users_data = await get_users(sftp, "usercache.json")
        uuid_to_name = {item["uuid"]: item["name"] for item in users_data}
        
        # 2. Lister tous les fichiers de stats
        stats_files = await sftp.glob("world/stats/*.json")
        logger.debug(f"Found {len(stats_files)} player stat files")
        
        # 3. Traiter en batch avec limite de concurrence
        semaphore = asyncio.Semaphore(5)  # Limite à 5 connexions simultanées
        tasks = []
        
        for stats_file in stats_files:
            uuid = stats_file.removeprefix("world/stats/").removesuffix(".json")
            nbt_file = f"world/playerdata/{uuid}.dat"
            player_name = uuid_to_name.get(uuid, uuid)
            
            task = get_single_player_stats_with_semaphore(
                semaphore, sftp, stats_file, nbt_file, player_name, uuid
            )
            tasks.append(task)
        
        # 4. Exécuter tous les tasks en parallèle
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 5. Filtrer les résultats valides
        valid_results = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Error processing player stats: {result}")
            elif result is not None:
                valid_results.append(result)
        
        logger.debug(f"Successfully processed {len(valid_results)} players")
        return valid_results
        
    except Exception as e:
        logger.error(f"Error in get_all_player_stats_optimized: {e}")
        raise

async def get_single_player_stats_with_semaphore(semaphore, sftp, stats_file, nbt_file, player_name, uuid):
    """Traite les stats d'un seul joueur avec limitation de concurrence"""
    async with semaphore:
        return await get_player_stats_optimized(sftp, stats_file, nbt_file, player_name, uuid)

async def get_player_stats_optimized(sftp: asyncssh.SFTPClient, stats_file, nbt_file, player_name, uuid):
    """Version optimisée pour traiter les stats d'un joueur avec cache"""
    cache_key = f"player_stats_{uuid}"
    
    # Vérifier le cache
    cached_data = stats_cache.get(cache_key)
    if cached_data is not None:
        logger.debug(f"Using cached data for player {player_name}")
        return cached_data
    
    try:
        # Lire les deux fichiers en parallèle
        stats_task = asyncio.create_task(read_stats_file(sftp, stats_file))
        nbt_task = asyncio.create_task(read_nbt_file(sftp, nbt_file))
        
        playerdata, nbt = await asyncio.gather(stats_task, nbt_task)
        
        # Extraction optimisée des données
        custom_stats = playerdata.get("stats", {}).get("minecraft:custom", {})
        mined_stats = playerdata.get("stats", {}).get("minecraft:mined", {})
        
        level = str(int(nbt[""]["XpLevel"]))
        deaths = custom_stats.get("minecraft:deaths", 0)
        playtime = custom_stats.get("minecraft:play_time", 0)
        walked = custom_stats.get("minecraft:walk_one_cm", 0)
        
        # Calculer le nombre total de blocs minés
        total_mined = sum(mined_stats.values()) if mined_stats else 0
        
        # Utiliser la statistique mob_kills qui est plus précise
        mob_kills = custom_stats.get("minecraft:mob_kills", 0)
        
        # Statistique des animaux reproduits
        animals_bred = custom_stats.get("minecraft:animals_bred", 0)
        
        # Calculs
        walked = walked / 100000
        playtime_seconds = playtime / 20
        ratio = deaths / max(playtime_seconds / 3600, 0.01)  # Éviter division par zéro
        
        player_data = {
            "Joueur": player_name,
            "Niveau": level,
            "Morts": deaths,
            "Morts/h": ratio,
            "Marche (km)": walked,
            "Temps de jeu": playtime_seconds,
            "Blocs minés": total_mined,
            "Mobs tués": mob_kills,
            "Animaux reproduits": animals_bred,
        }
        
        # Mettre en cache
        stats_cache.set(cache_key, player_data)
        logger.debug(f"Processed and cached data for player {player_name}")
        
        return player_data
        
    except Exception as e:
        logger.warning(f"Error processing stats for {player_name}: {e}")
        return None

async def read_stats_file(sftp: asyncssh.SFTPClient, stats_file):
    """Lit un fichier de statistiques JSON"""
    async with sftp.open(stats_file) as f:
        logger.debug(f"Reading {stats_file}")
        data = await f.read()
        return json.loads(data)

async def read_nbt_file(sftp: asyncssh.SFTPClient, nbt_file):
    """Lit un fichier NBT compressé"""
    async with sftp.open(nbt_file, 'rb') as f:
        logger.debug(f"Reading {nbt_file}")
        data = await f.read()
        return nbtlib.File.parse(gzip.GzipFile(fileobj=BytesIO(data)))

# Fonction de compatibilité avec l'ancien code
async def get_player_stats(sftp: asyncssh.SFTPClient, file, nbtfile=None):
    """Fonction de compatibilité - redirige vers la version optimisée"""
    uuid = file.removeprefix("world/stats/").removesuffix(".json")
    player_name = uuid  # Nom par défaut, sera remplacé par le vrai nom si disponible
    
    return await get_player_stats_optimized(sftp, file, nbtfile, player_name, uuid)
        
async def create_sftp_connection(host, port, username, password):
    async with asyncssh.connect(host, port=port, username=username, password=password, known_hosts=None) as conn:
        async with conn.start_sftp_client() as sftp:
            return sftp
            
# Fonction utilitaire pour gérer les connexions SFTP de manière robuste
async def create_optimized_sftp_connection(host, port, username, password, max_retries=3, timeout=30):
    """
    Crée une connexion SFTP optimisée avec retry automatique et timeout
    """
    for attempt in range(max_retries):
        try:
            logger.debug(f"Tentative de connexion SFTP {attempt + 1}/{max_retries}")
            
            conn = await asyncio.wait_for(
                asyncssh.connect(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    known_hosts=None,
                    connect_timeout=timeout,
                    keepalive_interval=15,  # Maintenir la connexion active
                    keepalive_count_max=3
                ),
                timeout=timeout
            )
            
            sftp = await conn.start_sftp_client()
            logger.debug("Connexion SFTP établie avec succès")
            return conn, sftp
            
        except asyncio.TimeoutError:
            logger.warning(f"Timeout lors de la connexion SFTP (tentative {attempt + 1})")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)  # Backoff exponentiel
            
        except Exception as e:
            logger.warning(f"Erreur lors de la connexion SFTP: {e} (tentative {attempt + 1})")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)

async def get_minecraft_stats_with_retry(host, port, username, password):
    """
    Récupère les statistiques Minecraft avec gestion d'erreurs robuste
    """
    conn = None
    try:
        conn, sftp = await create_optimized_sftp_connection(host, port, username, password)
        
        # Utiliser la fonction optimisée
        results = await get_all_player_stats_optimized(sftp)
        
        logger.info(f"Statistiques récupérées avec succès pour {len(results)} joueurs")
        return results
        
    except Exception as e:
        logger.error(f"Impossible de récupérer les statistiques: {e}")
        # Vider le cache en cas d'erreur pour forcer un reload au prochain essai
        stats_cache.clear()
        raise
        
    finally:
        if conn:
            try:
                conn.close()
                await conn.wait_closed()
            except Exception as e:
                logger.debug(f"Erreur lors de la fermeture de la connexion: {e}")

def format_number(num):
    if num >= 1000000:
        return f"{num/1000000:.2f}M"
    elif num >= 1000:
        return f"{num/1000:.2f}k"
    else:
        return str(int(num))
    
def calculate_level(xp):
    level = 0
    while xp >= 150*(1.05**(1.55*level)):
        xp -= 150*(1.05**(1.55*level))
        level += 1
    return str(level)
