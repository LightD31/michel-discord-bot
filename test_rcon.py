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

async def test_player_stats(host, port, password):
    """Test de récupération des statistiques des joueurs"""
    print(f"\n📊 Test de récupération des statistiques...")
    
    try:
        stats = await get_all_player_stats_rcon(host, port, password)
        
        if stats:
            print(f"✅ Statistiques récupérées pour {len(stats)} joueur(s):")
            for player_stat in stats:
                print(f"  - {player_stat['Joueur']}: Niveau {player_stat['Niveau']}, {player_stat['Morts']} morts")
        else:
            print("❌ Aucune statistique récupérée (aucun joueur en ligne ou erreur)")
            
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
