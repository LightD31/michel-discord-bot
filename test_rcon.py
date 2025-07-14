"""
Script de test pour RCON Minecraft
Utilisez ce script pour tester votre connexion RCON avant d'utiliser l'extension
"""

import asyncio
import sys
import os

# Ajouter le répertoire parent au path pour importer src
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.minecraft_rcon import MinecraftRCON, get_all_player_stats_rcon, get_server_info_rcon

async def test_rcon_connection(host, port, password):
    """Test de base de la connexion RCON"""
    print(f"Test de connexion RCON vers {host}:{port}")
    
    rcon = MinecraftRCON(host, port, password)
    
    try:
        print("Tentative de connexion...")
        if await rcon.connect():
            print("✅ Connexion RCON réussie!")
            
            # Test de commandes basiques
            commands = [
                "list",
                "time query gametime", 
                "forge tps",
                "forge chunks"
            ]
            
            for cmd in commands:
                print(f"\n🔧 Test de la commande: {cmd}")
                response = await rcon.execute_command(cmd)
                if response:
                    print(f"✅ Réponse: {response[:100]}{'...' if len(response) > 100 else ''}")
                else:
                    print("❌ Pas de réponse")
        else:
            print("❌ Échec de la connexion RCON")
            
    except Exception as e:
        print(f"❌ Erreur: {e}")
    finally:
        await rcon.disconnect()

async def test_player_stats_detailed(host, port, password, player_name):
    """Test détaillé de récupération des statistiques d'un joueur spécifique"""
    print(f"\n🔍 Test détaillé des statistiques pour le joueur: {player_name}")
    
    rcon = MinecraftRCON(host, port, password)
    
    try:
        if not await rcon.connect():
            print("❌ Impossible de se connecter")
            return
            
        # Tester chaque commande individuellement
        commands_to_test = [
            f"data get entity {player_name} XpLevel",
            f"data get entity {player_name} Stats.\"minecraft:custom\".\"minecraft:deaths\"",
            f"data get entity {player_name} Stats.\"minecraft:custom\".\"minecraft:play_time\"",
            f"data get entity {player_name} Stats.\"minecraft:custom\".\"minecraft:walk_one_cm\"",
            f"execute as {player_name} run scoreboard players get @s minecraft.custom:minecraft.deaths",
            f"execute as {player_name} run scoreboard players get @s minecraft.custom:minecraft.play_time",
            f"execute as {player_name} run scoreboard players get @s minecraft.custom:minecraft.walk_one_cm"
        ]
        
        for cmd in commands_to_test:
            print(f"\n🔧 Test: {cmd}")
            response = await rcon.execute_command(cmd)
            if response:
                print(f"✅ Réponse: {response}")
            else:
                print("❌ Pas de réponse")
        
        # Tester aussi quelques commandes de debugging
        debug_commands = [
            f"data get entity {player_name}",  # Toutes les données du joueur
            "scoreboard objectives list",      # Liste des objectives
        ]
        
        for cmd in debug_commands:
            print(f"\n� Debug: {cmd}")
            response = await rcon.execute_command(cmd)
            if response:
                # Limiter la sortie pour éviter le spam
                if len(response) > 200:
                    print(f"✅ Réponse (tronquée): {response[:200]}...")
                else:
                    print(f"✅ Réponse: {response}")
            else:
                print("❌ Pas de réponse")
                
    except Exception as e:
        print(f"❌ Erreur: {e}")
    finally:
        await rcon.disconnect()

async def test_player_stats(host, port, password):
    """Test de récupération des statistiques des joueurs"""
    print(f"\n�📊 Test de récupération des statistiques...")
    
    try:
        # D'abord récupérer la liste des joueurs
        rcon = MinecraftRCON(host, port, password)
        if await rcon.connect():
            response = await rcon.execute_command("list")
            print(f"Joueurs en ligne: {response}")
            
            if response and "online:" in response:
                players_part = response.split("online:")[1].strip()
                if players_part:
                    players = [name.strip() for name in players_part.split(",")]
                    print(f"Joueurs détectés: {players}")
                    
                    # Tester les stats pour chaque joueur
                    for player in players:
                        await test_player_stats_detailed(host, port, password, player)
                else:
                    print("Aucun joueur en ligne pour tester les stats")
            else:
                print("Format de réponse inattendu pour la commande list")
                
            await rcon.disconnect()
        
        # Ensuite tester la fonction complète
        stats = await get_all_player_stats_rcon(host, port, password)
        
        if stats:
            print(f"✅ Statistiques récupérées pour {len(stats)} joueur(s):")
            for player_stat in stats:
                print(f"  - {player_stat['Joueur']}: Niveau {player_stat['Niveau']}, {player_stat['Morts']} morts, {player_stat['Temps de jeu']}s de jeu")
        else:
            print("❌ Aucune statistique récupérée")
            
    except Exception as e:
        print(f"❌ Erreur lors de la récupération des stats: {e}")

async def main():
    """Fonction principale de test"""
    # Remplacez ces valeurs par vos paramètres RCON
    RCON_HOST = "127.0.0.1"  # ou votre IP
    RCON_PORT = 25575
    RCON_PASSWORD = "votre_mot_de_passe"
    
    print("🧪 Script de test RCON Minecraft")
    print("=" * 50)
    
    # Test de connexion de base
    await test_rcon_connection(RCON_HOST, RCON_PORT, RCON_PASSWORD)
    
    # Test des statistiques des joueurs
    await test_player_stats(RCON_HOST, RCON_PORT, RCON_PASSWORD)
    
    print("\n✅ Tests terminés!")

if __name__ == "__main__":
    # Vérifier que les paramètres sont configurés
    print("⚠️  N'oubliez pas de modifier les paramètres RCON dans ce script!")
    print("   RCON_HOST, RCON_PORT, et RCON_PASSWORD")
    print()
    
    asyncio.run(main())
