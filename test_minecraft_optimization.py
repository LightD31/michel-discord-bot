#!/usr/bin/env python3
"""
Script de test pour mesurer les performances des optimisations SFTP
"""

import asyncio
import time
import sys
import os

# Ajouter le répertoire parent au path pour les imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.minecraft import (
    get_minecraft_stats_with_retry,
    get_all_player_stats_optimized,
    stats_cache
)

async def test_optimized_connection():
    """Test de la connexion optimisée"""
    print("🧪 Test de la connexion SFTP optimisée...")
    
    # Configuration de test (remplacez par vos vraies valeurs)
    HOST = "192.168.0.126"
    PORT = 2224
    USERNAME = "Discord"
    PASSWORD = "your_password"  # Remplacez par le vrai mot de passe
    
    try:
        # Test 1: Premier appel (sans cache)
        print("📊 Premier appel (sans cache)...")
        start_time = time.time()
        
        results1 = await get_minecraft_stats_with_retry(HOST, PORT, USERNAME, PASSWORD)
        
        first_call_time = time.time() - start_time
        print(f"✅ Premier appel réussi: {len(results1)} joueurs en {first_call_time:.2f}s")
        
        # Test 2: Deuxième appel (avec cache)
        print("📊 Deuxième appel (avec cache)...")
        start_time = time.time()
        
        results2 = await get_minecraft_stats_with_retry(HOST, PORT, USERNAME, PASSWORD)
        
        second_call_time = time.time() - start_time
        print(f"✅ Deuxième appel réussi: {len(results2)} joueurs en {second_call_time:.2f}s")
        
        # Comparaison des performances
        if second_call_time < first_call_time:
            speedup = first_call_time / second_call_time
            print(f"🚀 Amélioration de performance: {speedup:.1f}x plus rapide avec le cache")
        
        # Afficher quelques statistiques
        if results1:
            print("\n📈 Aperçu des données récupérées:")
            for i, player in enumerate(results1[:3]):  # Afficher les 3 premiers
                print(f"  {i+1}. {player.get('Joueur', 'N/A')} - "
                      f"Niveau {player.get('Niveau', 0)} - "
                      f"Temps: {player.get('Temps de jeu', 0):.0f}s")
            
            if len(results1) > 3:
                print(f"  ... et {len(results1) - 3} autres joueurs")
        
        return True
        
    except Exception as e:
        print(f"❌ Erreur lors du test: {e}")
        return False

async def test_cache_efficiency():
    """Test de l'efficacité du cache"""
    print("\n🧪 Test de l'efficacité du cache...")
    
    # Vider le cache
    stats_cache.clear()
    print("🗑️ Cache vidé")
    
    # Simuler plusieurs appels
    cache_hits = 0
    cache_misses = 0
    
    for i in range(5):
        key = f"test_key_{i % 3}"  # Répéter certaines clés
        
        if stats_cache.is_valid(key):
            cache_hits += 1
            print(f"  Appel {i+1}: Cache HIT pour {key}")
        else:
            cache_misses += 1
            stats_cache.set(key, f"data_{i}")
            print(f"  Appel {i+1}: Cache MISS pour {key}")
    
    hit_rate = cache_hits / (cache_hits + cache_misses) * 100
    print(f"📊 Taux de cache hits: {hit_rate:.1f}% ({cache_hits}/{cache_hits + cache_misses})")

def test_performance_monitoring():
    """Test du monitoring des performances"""
    print("\n🧪 Test du monitoring des performances...")
    
    class PerformanceMonitor:
        def __init__(self):
            self.start_time = None
            self.operations = []
        
        def start_operation(self, name):
            self.start_time = time.time()
            print(f"⏱️ Début: {name}")
        
        def end_operation(self, name):
            if self.start_time:
                duration = time.time() - self.start_time
                self.operations.append((name, duration))
                print(f"✅ Fin: {name} en {duration:.3f}s")
                self.start_time = None
        
        def get_report(self):
            if not self.operations:
                return "Aucune opération enregistrée"
            
            total_time = sum(op[1] for op in self.operations)
            report = f"📊 Rapport de performance (Total: {total_time:.3f}s):\n"
            
            for name, duration in self.operations:
                percentage = (duration / total_time) * 100
                report += f"  • {name}: {duration:.3f}s ({percentage:.1f}%)\n"
            
            return report
    
    # Test du monitoring
    monitor = PerformanceMonitor()
    
    monitor.start_operation("Connexion SFTP")
    time.sleep(0.1)  # Simuler une opération
    monitor.end_operation("Connexion SFTP")
    
    monitor.start_operation("Lecture fichiers")
    time.sleep(0.05)  # Simuler une opération
    monitor.end_operation("Lecture fichiers")
    
    monitor.start_operation("Traitement données")
    time.sleep(0.02)  # Simuler une opération
    monitor.end_operation("Traitement données")
    
    print(monitor.get_report())

async def main():
    """Fonction principale de test"""
    print("🚀 Démarrage des tests d'optimisation SFTP Minecraft\n")
    
    # Test du cache
    await test_cache_efficiency()
    
    # Test du monitoring
    test_performance_monitoring()
    
    # Test de la connexion (commenté car nécessite les vraies credentials)
    print("\n⚠️ Test de connexion SFTP désactivé (nécessite les vraies credentials)")
    print("   Pour l'activer, modifiez les credentials dans test_optimized_connection()")
    
    # Uncomment pour tester avec de vraies credentials:
    # success = await test_optimized_connection()
    # if success:
    #     print("\n✅ Tous les tests sont passés!")
    # else:
    #     print("\n❌ Certains tests ont échoué")
    
    print("\n🏁 Tests terminés")

if __name__ == "__main__":
    asyncio.run(main())
