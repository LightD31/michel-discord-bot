import asyncio
import gzip
import json
import time
from io import BytesIO
from typing import cast

import asyncssh
import nbtlib

from src.core import logging as logutil
from src.core.logging import log_failure

logger = logutil.init_logger(__name__)


def _as_text(path: bytes | str) -> str:
    """Normalize an SFTP path to ``str``.

    asyncssh's glob() is typed as returning bytes even for a str pattern,
    and returns whichever the pattern was at runtime.
    """
    return path.decode("utf-8", "replace") if isinstance(path, bytes) else path


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


# Un cache par serveur (hôte:port) : deux serveurs ont chacun leur usercache.json
# et leurs joueurs, que des clés communes mélangeraient.
_stats_caches: dict[str, MinecraftStatsCache] = {}


def stats_cache_for(host: str, port: int | str) -> MinecraftStatsCache:
    """Le cache des statistiques lues sur le serveur *host*:*port*."""
    return _stats_caches.setdefault(f"{host}:{port}", MinecraftStatsCache())


def clear_stats_caches() -> None:
    """Vide le cache de chaque serveur."""
    for cache in _stats_caches.values():
        cache.clear()


def ticks_to_hms(ticks):
    seconds = ticks // 20  # Convert ticks to seconds
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


async def get_users(sftp: asyncssh.SFTPClient, file, cache: MinecraftStatsCache):
    """Récupère la liste des utilisateurs avec cache"""
    cache_key = f"users_{file}"

    # Vérifier le cache
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        logger.debug(f"Using cached user data for {file}")
        return cached_data

    # Lire depuis le serveur
    async with sftp.open(file) as f:
        data = await f.read()
        userdata = json.loads(data)

    # Mettre en cache
    cache.set(cache_key, userdata)
    logger.debug(f"Cached user data for {file}")
    return userdata


async def get_all_player_stats_optimized(sftp: asyncssh.SFTPClient, cache: MinecraftStatsCache):
    """Version optimisée qui traite tous les joueurs en une seule fois"""
    try:
        # 1. Récupérer la liste des utilisateurs
        users_data = await get_users(sftp, "usercache.json", cache)
        uuid_to_name = {item["uuid"]: item["name"] for item in users_data}

        # 2. Lister tous les fichiers de stats
        stats_files = await sftp.glob("world/stats/*.json")
        logger.debug(f"Found {len(stats_files)} player stat files")

        # 3. Traiter en batch avec limite de concurrence
        semaphore = asyncio.Semaphore(5)  # Limite à 5 connexions simultanées
        tasks = []

        for stats_file in stats_files:
            uuid = _as_text(stats_file).removeprefix("world/stats/").removesuffix(".json")
            nbt_file = f"world/playerdata/{uuid}.dat"
            player_name = uuid_to_name.get(uuid, uuid)

            task = get_single_player_stats_with_semaphore(
                semaphore, sftp, stats_file, nbt_file, player_name, uuid, cache
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
        log_failure(logger, f"Error in get_all_player_stats_optimized: {e}")
        raise


async def get_single_player_stats_with_semaphore(
    semaphore, sftp, stats_file, nbt_file, player_name, uuid, cache: MinecraftStatsCache
):
    """Traite les stats d'un seul joueur avec limitation de concurrence"""
    async with semaphore:
        return await get_player_stats_optimized(
            sftp, stats_file, nbt_file, player_name, uuid, cache
        )


async def get_player_stats_optimized(
    sftp: asyncssh.SFTPClient, stats_file, nbt_file, player_name, uuid, cache: MinecraftStatsCache
):
    """Version optimisée pour traiter les stats d'un joueur avec cache"""
    cache_key = f"player_stats_{uuid}"

    # Vérifier le cache
    cached_data = cache.get(cache_key)
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

        # Support both named-root ({"": {...}}) and unnamed-root NBT layouts
        nbt_root = nbt.get("", nbt)
        level = str(int(nbt_root["XpLevel"]))
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
        cache.set(cache_key, player_data)
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
    async with sftp.open(nbt_file, "rb") as f:
        logger.debug(f"Reading {nbt_file}")
        data = await f.read()
        # asyncssh's stubs type read() as str regardless of the "rb" mode the
        # file was opened with; the payload here is the raw gzip stream.
        return nbtlib.File.parse(gzip.GzipFile(fileobj=BytesIO(cast(bytes, data))))


_KEY_TYPE_PREFIXES = ("ssh-", "ecdsa-", "sk-")


def trusted_host_keys(host_key: str) -> tuple[list[asyncssh.SSHKey], list, list]:
    """The ``known_hosts`` argument that trusts exactly *host_key* and nothing else.

    *host_key* is the server's public key in OpenSSH format
    (``ssh-ed25519 AAAA…``). A line copied from ``ssh-keyscan`` or a
    ``known_hosts`` file, which starts with the host name, is accepted too.
    Raises ``ValueError`` when no usable key is given: the connection is then
    refused rather than made to an unauthenticated host.
    """
    tokens = host_key.split()
    if tokens and not tokens[0].startswith(_KEY_TYPE_PREFIXES):
        tokens = tokens[1:]
    if len(tokens) < 2:
        raise ValueError("clé d'hôte SFTP manquante ou illisible")
    try:
        key = asyncssh.import_public_key(" ".join(tokens[:2]))
    except asyncssh.KeyImportError as e:
        raise ValueError(f"clé d'hôte SFTP illisible : {e}") from e
    return [key], [], []


# Fonction utilitaire pour gérer les connexions SFTP de manière robuste
async def create_optimized_sftp_connection(
    host, port, username, password, host_key, max_retries=3, timeout=30
):
    """
    Crée une connexion SFTP optimisée avec retry automatique et timeout.

    Le serveur doit présenter la clé *host_key* (voir :func:`trusted_host_keys`).
    """
    known_hosts = trusted_host_keys(host_key)
    for attempt in range(max_retries):
        try:
            logger.debug(f"Tentative de connexion SFTP {attempt + 1}/{max_retries}")

            conn = await asyncio.wait_for(
                asyncssh.connect(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    known_hosts=known_hosts,
                    connect_timeout=timeout,
                    keepalive_interval=15,  # Maintenir la connexion active
                    keepalive_count_max=3,
                ),
                timeout=timeout,
            )

            sftp = await conn.start_sftp_client()
            logger.debug("Connexion SFTP établie avec succès")
            return conn, sftp

        except TimeoutError:
            logger.warning(f"Timeout lors de la connexion SFTP (tentative {attempt + 1})")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2**attempt)  # Backoff exponentiel

        except Exception as e:
            logger.warning(f"Erreur lors de la connexion SFTP: {e} (tentative {attempt + 1})")
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2**attempt)


async def get_minecraft_stats_with_retry(host, port, username, password, host_key):
    """
    Récupère les statistiques Minecraft avec gestion d'erreurs robuste
    """
    conn = None
    cache = stats_cache_for(host, port)
    try:
        conn, sftp = await create_optimized_sftp_connection(
            host, port, username, password, host_key
        )

        # Utiliser la fonction optimisée
        results = await get_all_player_stats_optimized(sftp, cache)

        logger.info(f"Statistiques récupérées avec succès pour {len(results)} joueurs")
        return results

    except Exception as e:
        log_failure(logger, f"Impossible de récupérer les statistiques: {e}")
        # Vider le cache en cas d'erreur pour forcer un reload au prochain essai
        cache.clear()
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
        return f"{num / 1000000:.2f}M"
    elif num >= 1000:
        return f"{num / 1000:.2f}k"
    else:
        return str(int(num))


def calculate_level(xp):
    level = 0
    while xp >= 150 * (1.05 ** (1.55 * level)):
        xp -= 150 * (1.05 ** (1.55 * level))
        level += 1
    return str(level)
