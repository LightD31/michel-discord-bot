from io import BytesIO
import json
import asyncssh
import os
from src import logutil

logger = logutil.init_logger(os.path.basename(__file__))

def ticks_to_hms(ticks):
    seconds = ticks // 20  # Convert ticks to seconds
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

async def get_users(sftp:asyncssh.SFTPClient, file):
    async with sftp.open(file) as f:
        data = await f.read()
        userdata = json.loads(data)
        return userdata

async def get_player_stats(sftp:asyncssh.SFTPClient, file, nbtfile):
    async with sftp.open(file) as f:
        data = await f.read()
        playerdata = json.loads(data)
    skills = nbtfile["data"]["xp_data"][str(file).removesuffix(".json").removeprefix("world/stats/")]
    deaths = (
        playerdata.get("stats", {})
        .get("minecraft:custom", {})
        .get("minecraft:deaths", 0)
    )
    playtime = (
        playerdata.get("stats", {})
        .get("minecraft:custom", {})
        .get("minecraft:play_time", 0)
    )
    walked = (
        playerdata.get("stats", {})
        .get("minecraft:custom", {})
        .get("minecraft:walk_one_cm", 0)
    )
    # quartz = (
    #     playerdata.get("stats", {})
    #     .get("minecraft:mined", {})
    #     .get("minecraft:nether_quartz_ore", 0)
    # )
    # icePlaced = playerdata.get("stats", {}).get("minecraft:used", {}).get("minecraft:ice", 0)
    # BlueIcePlaced = playerdata.get("stats", {}).get("minecraft:used", {}).get("minecraft:blue_ice", 0)

    walked = walked / 100000
    ratio = deaths / (playtime / 20 / 60 / 60)
    playtime = playtime / 20

    player_data = {
        "Joueur": str(file).removesuffix(".json").removeprefix("world/stats/"),
        "Morts": deaths,
        "Temps de jeu": playtime,
        "Morts/h": ratio,
        "Marche (km)": walked,
        # "Quartz minés": quartz,
        # "Glace posée": icePlaced+BlueIcePlaced,
    }
    for skill in sorted(skills):
        player_data[str(skill).capitalize()] = calculate_level(skills[skill])
    return player_data
        
async def create_sftp_connection(host, port, username, password):
    async with asyncssh.connect(host, port=port, username=username, password=password, known_hosts=None) as conn:
        async with conn.start_sftp_client() as sftp:
            return sftp
            
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
