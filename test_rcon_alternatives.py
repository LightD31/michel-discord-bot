"""
Alternative RCON implementation that tries different approaches for stats
"""

import asyncio
import sys
import os

# Ajouter le répertoire parent au path pour importer src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.minecraft_rcon import MinecraftRCON

async def get_player_stats_alternative(rcon_client, player_name):
    """Version alternative pour récupérer les stats"""
    stats = {}
    
    # Approche 1: Utiliser data get avec le chemin complet
    print(f"\n🔍 Approche 1: data get pour {player_name}")
    
    level_response = await rcon_client.execute_command(f"data get entity {player_name} XpLevel")
    print(f"Niveau: {level_response}")
    
    # Approche 2: Essayer sans quotes autour des chemins
    alt_commands = {
        "deaths": f"data get entity {player_name} Stats.minecraft:custom.minecraft:deaths",
        "playtime": f"data get entity {player_name} Stats.minecraft:custom.minecraft:play_time",
        "walked": f"data get entity {player_name} Stats.minecraft:custom.minecraft:walk_one_cm"
    }
    
    print(f"\n🔍 Approche 2: Sans quotes pour {player_name}")
    for stat_name, command in alt_commands.items():
        response = await rcon_client.execute_command(command)
        print(f"{stat_name}: {response}")
    
    # Approche 3: Essayer avec des crochets
    bracket_commands = {
        "deaths": f"data get entity {player_name} Stats[minecraft:custom][minecraft:deaths]",
        "playtime": f"data get entity {player_name} Stats[minecraft:custom][minecraft:play_time]",
        "walked": f"data get entity {player_name} Stats[minecraft:custom][minecraft:walk_one_cm]"
    }
    
    print(f"\n🔍 Approche 3: Avec crochets pour {player_name}")
    for stat_name, command in bracket_commands.items():
        response = await rcon_client.execute_command(command)
        print(f"{stat_name}: {response}")
    
    # Approche 4: Regarder toute la structure Stats
    print(f"\n🔍 Approche 4: Structure complète des Stats pour {player_name}")
    stats_response = await rcon_client.execute_command(f"data get entity {player_name} Stats")
    if stats_response:
        print(f"Stats complètes: {stats_response[:500]}...")  # Limiter la sortie
    
    # Approche 5: Essayer playerdata directement (si c'est possible)
    print(f"\n🔍 Approche 5: Tentative playerdata pour {player_name}")
    uuid_response = await rcon_client.execute_command(f"data get entity {player_name} UUID")
    print(f"UUID: {uuid_response}")

async def test_all_approaches(host, port, password):
    """Test toutes les approches possibles"""
    rcon = MinecraftRCON(host, port, password)
    
    try:
        if not await rcon.connect():
            print("❌ Impossible de se connecter")
            return
            
        # Récupérer les joueurs en ligne
        list_response = await rcon.execute_command("list")
        print(f"Joueurs en ligne: {list_response}")
        
        if list_response and "online:" in list_response:
            players_part = list_response.split("online:")[1].strip()
            if players_part:
                players = [name.strip() for name in players_part.split(",")]
                
                for player in players:
                    print(f"\n{'='*50}")
                    print(f"Test pour le joueur: {player}")
                    print(f"{'='*50}")
                    await get_player_stats_alternative(rcon, player)
            else:
                print("Aucun joueur en ligne")
        else:
            print("Impossible de récupérer la liste des joueurs")
            
    except Exception as e:
        print(f"❌ Erreur: {e}")
    finally:
        await rcon.disconnect()

async def main():
    # Remplacez ces valeurs par vos paramètres RCON
    RCON_HOST = "127.0.0.1"
    RCON_PORT = 25575
    RCON_PASSWORD = "votre_mot_de_passe"
    
    print("🧪 Test des différentes approches pour récupérer les stats")
    print("=" * 60)
    
    await test_all_approaches(RCON_HOST, RCON_PORT, RCON_PASSWORD)

if __name__ == "__main__":
    print("⚠️  N'oubliez pas de modifier les paramètres RCON dans ce script!")
    print("   RCON_HOST, RCON_PORT, et RCON_PASSWORD")
    print()
    
    asyncio.run(main())
