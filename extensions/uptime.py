"""
This module provides a Discord bot extension that sends a status update to a server at a specific time
and monitors specific sensors for maintenance notifications.
"""

import os
import json

import aiohttp
from interactions import (
    Extension, 
    listen, 
    Task, 
    IntervalTrigger, 
    Client,
    slash_command,
    SlashContext,
    slash_option,
    OptionType,
    Embed,
    BaseChannel
)

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleUptime")

class Uptime(Extension):
    """
    A Discord bot extension that sends a status update to a server at a specific time
    and monitors specific sensors for maintenance notifications using SocketIO API.
    """
    def __init__(self, bot):
        self.bot : Client = bot
        # Connexion SocketIO pour Uptime Kuma
        self.sio = None
        self.connected = False
        # Dictionnaire pour stocker les configurations de surveillance par serveur
        # Format: {guild_id: {sensor_id: {"channel_id": int, "last_status": str}}}
        self.maintenance_monitors = {}
        # Derniers états connus des capteurs pour éviter les notifications dupliquées
        self.sensor_states = {}
        # Cache des informations des moniteurs
        self.monitors_cache = {}
        # Liste des événements récents pour débogage
        self.recent_events = []

    @listen()
    async def on_startup(self):
        """
        Start background tasks and connect to Uptime Kuma via SocketIO.
        """
        await self.load_maintenance_monitors()
        await self.connect_socketio()
        self.send_status_update.start()
        self.check_sensor_maintenance.start()
        await self.send_status_update()

    async def connect_socketio(self):
        """
        Établit la connexion SocketIO avec Uptime Kuma.
        
        Note: L'authentification SocketIO utilise les credentials utilisateur (username/password)
        et non une clé API, conformément à la documentation officielle :
        https://github.com/louislam/uptime-kuma/wiki/API-Documentation
        """
        if not config.get('uptimeKuma', {}).get('uptimeKumaUrl'):
            logger.error("Configuration Uptime Kuma manquante pour SocketIO - URL requise")
            return
            
        if not config.get('uptimeKuma', {}).get('uptimeKumaUsername') or not config.get('uptimeKuma', {}).get('uptimeKumaPassword'):
            logger.error("Configuration Uptime Kuma manquante pour SocketIO - username et password requis")
            return

        try:
            # Import socketio ici pour éviter les erreurs si pas installé
            import socketio
            
            self.sio = socketio.AsyncClient()
            
            @self.sio.event
            async def connect():
                logger.info("Connexion SocketIO établie avec Uptime Kuma")
                self.connected = True
                # S'authentifier avec les credentials utilisateur
                if self.sio:
                    try:
                        response = await self.sio.call('login', {
                            'username': config.get('uptimeKuma', {}).get('uptimeKumaUsername'),
                            'password': config.get('uptimeKuma', {}).get('uptimeKumaPassword'),
                            'token': config.get('uptimeKuma', {}).get('uptimeKuma2FA', '')  # Token 2FA optionnel
                        })
                        
                        if response and response.get('ok'):
                            logger.info("Authentification SocketIO réussie")
                            # Demander la liste des moniteurs après authentification
                            await self.sio.emit('getMonitorList')
                            # S'abonner aux événements de tous les moniteurs surveillés
                            await self._subscribe_to_monitors()
                        else:
                            error_msg = response.get('msg', 'Erreur inconnue') if response else 'Aucune réponse reçue'
                            logger.error(f"Échec de l'authentification SocketIO: {error_msg}")
                            self.connected = False
                    except Exception as auth_error:
                        logger.error(f"Erreur lors de l'authentification SocketIO: {auth_error}")
                        self.connected = False

            @self.sio.event
            async def disconnect():
                logger.warning("Connexion SocketIO fermée")
                self.connected = False

            @self.sio.event
            async def monitor(data):
                """
                Reçoit les mises à jour des moniteurs en temps réel.
                """
                await self._record_event('monitor', data)
                logger.debug(f"Événement monitor reçu: {data}")
                await self.handle_monitor_update(data)

            @self.sio.event
            async def heartbeat(data):
                """
                Événement heartbeat - PRINCIPAL pour les mises à jour temps réel.
                Cet événement est utilisé par le frontend officiel d'Uptime Kuma.
                """
                await self._record_event('heartbeat', data)
                logger.debug(f"Événement heartbeat reçu: {data}")
                await self.handle_monitor_update(data)

            @self.sio.event
            async def monitorList(data):
                """
                Reçoit la liste des moniteurs.
                """
                await self._record_event('monitorList', data)
                self.monitors_cache = data
                logger.debug(f"Cache des moniteurs mis à jour: {len(data)} moniteurs")
                logger.debug(f"IDs des moniteurs reçus: {list(data.keys())}")
                
            @self.sio.event
            async def loginRequired():
                """
                Serveur indique qu'une authentification est requise.
                """
                logger.debug("Authentification requise par le serveur")
                
            @self.sio.event
            async def updateMonitorIntoList(data):
                """
                Met à jour les informations d'un moniteur spécifique dans le cache.
                """
                await self._record_event('updateMonitorIntoList', data)
                logger.debug(f"Mise à jour reçue pour moniteurs: {list(data.keys())}")
                for monitor_id, monitor_data in data.items():
                    self.monitors_cache[monitor_id] = monitor_data
                    logger.debug(f"Moniteur {monitor_id} mis à jour dans le cache")

            # Ajouter d'autres événements pour le débogage
            @self.sio.event
            async def info(data):
                """
                Événement d'information.
                """
                await self._record_event('info', data)
                logger.debug(f"Info reçue: {data}")

            @self.sio.event
            async def monitorBeat(data):
                """
                Événement de battement de moniteur (peut être utilisé au lieu de 'monitor').
                """
                await self._record_event('monitorBeat', data)
                logger.debug(f"MonitorBeat reçu: {data}")
                await self.handle_monitor_update(data)

            @self.sio.event
            async def uptime(monitorID, period, percent):
                """
                Événement uptime - statistiques d'uptime.
                Paramètres: monitorID, period (24h/30d), percent
                """
                data = {'monitorID': monitorID, 'period': period, 'percent': percent}
                await self._record_event('uptime', data)
                logger.debug(f"Uptime reçu: monitorID={monitorID}, period={period}, percent={percent}")

            @self.sio.event
            async def avgPing(monitorID, avgPing):
                """
                Événement avgPing - ping moyen.
                Paramètres: monitorID, avgPing
                """
                data = {'monitorID': monitorID, 'avgPing': avgPing}
                await self._record_event('avgPing', data)
                logger.debug(f"AvgPing reçu: monitorID={monitorID}, avgPing={avgPing}")

            @self.sio.event
            async def heartbeatList(monitorID, heartbeatList):
                """
                Événement heartbeatList - liste des heartbeats.
                Paramètres: monitorID, heartbeatList
                """
                data = {'monitorID': monitorID, 'heartbeatList': heartbeatList}
                await self._record_event('heartbeatList', data)
                logger.debug(f"HeartbeatList reçu: monitorID={monitorID}, count={len(heartbeatList) if heartbeatList else 0}")

            # Événement générique pour capturer tous les autres événements
            @self.sio.event
            async def connect_error(data):
                logger.error(f"Erreur de connexion SocketIO: {data}")

            # Se connecter au serveur Uptime Kuma
            url = f"https://{config['uptimeKuma']['uptimeKumaUrl']}"
            await self.sio.connect(url, transports=['websocket', 'polling'])
            
        except ImportError:
            logger.error("Module 'socketio' non disponible. Utilisez: pip install python-socketio")
        except Exception as error:
            logger.error(f"Erreur lors de la connexion SocketIO: {error}")

    async def _record_event(self, event_name: str, data):
        """
        Enregistre les événements SocketIO reçus pour le débogage.
        """
        import time
        import json
        
        try:
            # Limiter la taille de la liste à 50 événements
            if len(self.recent_events) >= 50:
                self.recent_events.pop(0)
            
            # Convertir les données en string pour l'affichage
            data_str = json.dumps(data, default=str)[:200]  # Limiter à 200 caractères
            
            self.recent_events.append({
                'timestamp': time.time(),
                'event': event_name,
                'data': data_str
            })
        except Exception as e:
            logger.debug(f"Erreur lors de l'enregistrement de l'événement {event_name}: {e}")

    async def _subscribe_to_monitors(self):
        """
        S'abonne aux mises à jour des moniteurs surveillés.
        """
        try:
            if not self.connected or not self.sio:
                logger.warning("Pas de connexion SocketIO active pour s'abonner aux moniteurs")
                return
                
            # S'abonner aux événements de tous les moniteurs surveillés
            monitored_ids = set()
            for guild_monitors in self.maintenance_monitors.values():
                monitored_ids.update(guild_monitors.keys())
            
            if monitored_ids:
                logger.info(f"Abonnement aux moniteurs: {monitored_ids}")
                # Certaines versions d'Uptime Kuma utilisent des événements différents
                # Nous essayons plusieurs approches
                for monitor_id in monitored_ids:
                    try:
                        # Essayer de demander les détails du moniteur pour s'assurer qu'il existe
                        await self.sio.emit('getMonitor', int(monitor_id))
                    except Exception as e:
                        logger.debug(f"Erreur lors de la souscription au moniteur {monitor_id}: {e}")
            else:
                logger.info("Aucun moniteur à surveiller configuré")
                
        except Exception as error:
            logger.error(f"Erreur lors de l'abonnement aux moniteurs: {error}")

    async def handle_monitor_update(self, data):
        """
        Traite les mises à jour des moniteurs reçues via SocketIO.
        Gère les données des événements 'heartbeat', 'monitor', et 'monitorBeat'.
        """
        try:
            # Déterminer l'ID du moniteur selon le format des données
            monitor_id = None
            status = None
            
            # Format heartbeat (Uptime Kuma officiel)
            if 'monitorID' in data:
                monitor_id = str(data.get('monitorID'))
                status = data.get('status')
            # Format monitor alternatif
            elif 'id' in data:
                monitor_id = str(data.get('id'))
                status = data.get('status')
            
            if not monitor_id:
                logger.debug(f"Impossible de déterminer l'ID du moniteur dans les données: {data}")
                return
            
            logger.debug(f"Traitement mise à jour moniteur {monitor_id}: status={status}, data={data}")
            
            # Mettre à jour le cache avec plus d'informations
            if monitor_id in self.monitors_cache:
                # Fusionner les nouvelles données avec les existantes
                if isinstance(self.monitors_cache[monitor_id], dict):
                    self.monitors_cache[monitor_id].update(data)
                else:
                    self.monitors_cache[monitor_id] = data
                logger.debug(f"Cache mis à jour pour moniteur {monitor_id}")
            else:
                logger.debug(f"Moniteur {monitor_id} non trouvé dans le cache, ajout des données")
                self.monitors_cache[monitor_id] = data
            
            # Vérifier si ce moniteur est surveillé
            monitor_found = False
            for guild_id, sensors in self.maintenance_monitors.items():
                if monitor_id in sensors:
                    monitor_found = True
                    monitor_config = sensors[monitor_id]
                    last_status = monitor_config.get('last_status')
                    
                    logger.debug(f"Moniteur {monitor_id} surveillé - ancien status: {last_status}, nouveau: {status}")
                    
                    if last_status != status and status is not None:
                        logger.info(f"Changement d'état détecté pour moniteur {monitor_id}: {last_status} → {status}")
                        # Récupérer les infos complètes du moniteur
                        monitor_info = self.monitors_cache.get(monitor_id, data)
                        
                        await self._send_maintenance_notification(
                            guild_id, monitor_id, monitor_info, status, last_status, monitor_config
                        )
                        
                        # Mettre à jour le dernier état connu
                        self.maintenance_monitors[guild_id][monitor_id]['last_status'] = status
                        await self.save_maintenance_monitors()
                    else:
                        logger.debug(f"Pas de changement d'état pour moniteur {monitor_id} (status={status})")
            
            if not monitor_found:
                logger.debug(f"Moniteur {monitor_id} non surveillé par aucun serveur")
                        
        except Exception as error:
            logger.error(f"Erreur lors du traitement de la mise à jour du moniteur: {error}")

    @slash_command(
        name="setup_maintenance_alert",
        description="Configure les alertes de maintenance pour un capteur spécifique"
    )
    @slash_option(
        name="sensor_id",
        description="ID du capteur à surveiller",
        opt_type=OptionType.INTEGER,
        required=True
    )
    @slash_option(
        name="channel",
        description="Canal où envoyer les notifications",
        opt_type=OptionType.CHANNEL,
        required=True
    )
    async def setup_maintenance_alert(self, ctx: SlashContext, sensor_id: int, channel: BaseChannel):
        """
        Configure une alerte de maintenance pour un capteur spécifique dans un canal donné.
        """
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return

        # Vérifier si le module est activé sur ce serveur
        if str(ctx.guild.id) not in enabled_servers:
            await ctx.send("❌ Le module Uptime n'est pas activé sur ce serveur.", ephemeral=True)
            return
            
        guild_id = str(ctx.guild.id)
        
        # Vérifier que l'API Uptime Kuma est configurée
        if (not config.get('uptimeKuma', {}).get('uptimeKumaUrl') or 
            not config.get('uptimeKuma', {}).get('uptimeKumaUsername') or 
            not config.get('uptimeKuma', {}).get('uptimeKumaPassword')):
            await ctx.send("❌ Configuration Uptime Kuma manquante. Vérifiez l'URL, le nom d'utilisateur et le mot de passe.", ephemeral=True)
            return

        # Vérifier si le capteur existe
        sensor_info = await self._get_sensor_info(sensor_id)
        if not sensor_info:
            await ctx.send(f"❌ Capteur avec l'ID {sensor_id} introuvable.", ephemeral=True)
            return

        # Initialiser la structure si nécessaire
        if guild_id not in self.maintenance_monitors:
            self.maintenance_monitors[guild_id] = {}

        # Configurer la surveillance
        self.maintenance_monitors[guild_id][str(sensor_id)] = {
            "channel_id": channel.id,
            "last_status": None
        }

        # Sauvegarder la configuration
        await self.save_maintenance_monitors()

        embed = Embed(
            title="✅ Alerte de maintenance configurée",
            description=f"Les notifications de maintenance pour le capteur **{sensor_info.get('name', f'ID {sensor_id}')}** seront envoyées dans {channel.mention}",
            color=0x00FF00
        )
        await ctx.send(embed=embed)

    @slash_command(
        name="remove_maintenance_alert",
        description="Supprime les alertes de maintenance pour un capteur"
    )
    @slash_option(
        name="sensor_id",
        description="ID du capteur à ne plus surveiller",
        opt_type=OptionType.INTEGER,
        required=True
    )
    async def remove_maintenance_alert(self, ctx: SlashContext, sensor_id: int):
        """
        Supprime la surveillance de maintenance pour un capteur spécifique.
        """
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return

        # Vérifier si le module est activé sur ce serveur
        if str(ctx.guild.id) not in enabled_servers:
            await ctx.send("❌ Le module Uptime n'est pas activé sur ce serveur.", ephemeral=True)
            return
            
        guild_id = str(ctx.guild.id)
        sensor_id_str = str(sensor_id)

        if (guild_id not in self.maintenance_monitors or 
            sensor_id_str not in self.maintenance_monitors[guild_id]):
            await ctx.send(f"❌ Aucune alerte configurée pour le capteur ID {sensor_id}.", ephemeral=True)
            return

        del self.maintenance_monitors[guild_id][sensor_id_str]
        
        # Nettoyer si plus de surveillance pour ce serveur
        if not self.maintenance_monitors[guild_id]:
            del self.maintenance_monitors[guild_id]

        # Sauvegarder la configuration
        await self.save_maintenance_monitors()

        await ctx.send(f"✅ Alerte de maintenance supprimée pour le capteur ID {sensor_id}.")

    @slash_command(
        name="list_maintenance_alerts",
        description="Liste toutes les alertes de maintenance configurées"
    )
    async def list_maintenance_alerts(self, ctx: SlashContext):
        """
        Liste toutes les alertes de maintenance configurées pour ce serveur.
        """
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return

        # Vérifier si le module est activé sur ce serveur
        if str(ctx.guild.id) not in enabled_servers:
            await ctx.send("❌ Le module Uptime n'est pas activé sur ce serveur.", ephemeral=True)
            return
            
        guild_id = str(ctx.guild.id)

        if guild_id not in self.maintenance_monitors or not self.maintenance_monitors[guild_id]:
            await ctx.send("❌ Aucune alerte de maintenance configurée sur ce serveur.", ephemeral=True)
            return

        embed = Embed(
            title="📋 Alertes de maintenance configurées",
            color=0x0099FF
        )

        for sensor_id, config_data in self.maintenance_monitors[guild_id].items():
            channel = self.bot.get_channel(config_data["channel_id"])
            sensor_info = await self._get_sensor_info(int(sensor_id))
            sensor_name = sensor_info.get('name', f'ID {sensor_id}') if sensor_info else f'ID {sensor_id}'
            
            embed.add_field(
                name=f"Capteur: {sensor_name}",
                value=f"Canal: {channel.mention if channel else 'Canal introuvable'}",
                inline=False
            )

        await ctx.send(embed=embed)

    async def _get_sensor_info(self, sensor_id: int):
        """
        Récupère les informations d'un capteur depuis le cache SocketIO ou via requête SocketIO.
        """
        sensor_id_str = str(sensor_id)
        
        # Vérifier d'abord le cache
        if sensor_id_str in self.monitors_cache:
            return self.monitors_cache[sensor_id_str]
        
        # Si pas dans le cache et connexion SocketIO active, demander les infos
        if self.connected and self.sio:
            try:
                # Demander les informations du moniteur via SocketIO
                await self.sio.emit('getMonitor', sensor_id)
                
                # Attendre un court délai pour recevoir la réponse
                import asyncio
                await asyncio.sleep(0.5)
                
                # Vérifier à nouveau le cache après la requête
                if sensor_id_str in self.monitors_cache:
                    return self.monitors_cache[sensor_id_str]
                    
            except Exception as error:
                logger.error(f"Erreur lors de la récupération du moniteur {sensor_id} via SocketIO: {error}")
        
        # Fallback vers l'API REST si SocketIO n'est pas disponible
        try:
            async with aiohttp.ClientSession() as session:
                # Utiliser l'authentification basique avec username/password
                auth = aiohttp.BasicAuth(
                    config.get('uptimeKuma', {}).get('uptimeKumaUsername', ''),
                    config.get('uptimeKuma', {}).get('uptimeKumaPassword', '')
                )
                url = f"https://{config['uptimeKuma']['uptimeKumaUrl']}/api/monitor/{sensor_id}"
                
                async with session.get(url, auth=auth) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Mettre à jour le cache
                        self.monitors_cache[sensor_id_str] = data
                        return data
                    else:
                        logger.warning(f"Erreur API REST pour capteur {sensor_id}: {response.status}")
                        return None
        except Exception as error:
            logger.error(f"Erreur lors de la récupération du capteur {sensor_id} via API REST: {error}")
            return None

    @Task.create(IntervalTrigger(seconds=300))  # Réduit la fréquence car on utilise SocketIO pour le temps réel
    async def check_sensor_maintenance(self):
        """
        Vérifie périodiquement la connexion SocketIO et fait des vérifications de backup.
        Les mises à jour temps réel sont gérées par handle_monitor_update().
        """
        # Vérifier la connexion SocketIO
        if not self.connected and self.sio:
            logger.warning("Connexion SocketIO perdue, tentative de reconnexion...")
            try:
                await self.connect_socketio()
            except Exception as error:
                logger.error(f"Erreur lors de la reconnexion SocketIO: {error}")
        
        # Backup: vérification manuelle si SocketIO n'est pas connecté
        if not self.connected:
            logger.info("SocketIO non connecté, utilisation de la vérification manuelle")
            await self._manual_sensor_check()

    async def _manual_sensor_check(self):
        """
        Vérification manuelle des capteurs en cas de problème avec SocketIO.
        """
        if (not config.get('uptimeKuma', {}).get('uptimeKumaUrl') or 
            not config.get('uptimeKuma', {}).get('uptimeKumaUsername') or 
            not config.get('uptimeKuma', {}).get('uptimeKumaPassword')):
            return

        for guild_id, sensors in self.maintenance_monitors.items():
            for sensor_id, monitor_config in sensors.items():
                try:
                    sensor_info = await self._get_sensor_info(int(sensor_id))
                    if not sensor_info:
                        continue

                    current_status = sensor_info.get('status', 'unknown')
                    last_status = monitor_config.get('last_status')
                    
                    # Détecter les changements d'état significatifs
                    if last_status != current_status:
                        await self._send_maintenance_notification(
                            guild_id, sensor_id, sensor_info, current_status, last_status, monitor_config
                        )
                        # Mettre à jour le dernier état connu
                        self.maintenance_monitors[guild_id][sensor_id]['last_status'] = current_status
                        # Sauvegarder la configuration mise à jour
                        await self.save_maintenance_monitors()

                except Exception as error:
                    logger.error(f"Erreur lors de la vérification manuelle du capteur {sensor_id}: {error}")

    async def load_maintenance_monitors(self):
        """
        Charge les configurations de surveillance depuis le fichier JSON.
        """
        try:
            file_path = f"{config['misc']['dataFolder']}/uptime_maintenance_monitors.json"
            with open(file_path, "r", encoding="utf-8") as file:
                self.maintenance_monitors = json.load(file)
            logger.info(f"Configurations de surveillance chargées: {len(self.maintenance_monitors)} serveurs")
        except FileNotFoundError:
            logger.info("Aucun fichier de surveillance trouvé, démarrage avec une configuration vide")
            self.maintenance_monitors = {}
        except Exception as error:
            logger.error(f"Erreur lors du chargement des configurations: {error}")
            self.maintenance_monitors = {}

    async def save_maintenance_monitors(self):
        """
        Sauvegarde les configurations de surveillance dans le fichier JSON.
        """
        try:
            file_path = f"{config['misc']['dataFolder']}/uptime_maintenance_monitors.json"
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(self.maintenance_monitors, file, indent=4, ensure_ascii=False)
            logger.debug("Configurations de surveillance sauvegardées")
        except Exception as error:
            logger.error(f"Erreur lors de la sauvegarde des configurations: {error}")

    async def _send_maintenance_notification(self, guild_id: str, sensor_id: str, sensor_info: dict, 
                                           current_status: str, last_status: str, monitor_config: dict):
        """
        Envoie une notification de maintenance dans le canal configuré.
        Gère les statuts de l'API SocketIO (0=down, 1=up, 2=pending, 9=maintenance).
        """
        try:
            channel = self.bot.get_channel(monitor_config['channel_id'])
            if not channel:
                logger.warning(f"Canal {monitor_config['channel_id']} introuvable pour les notifications")
                return

            # Vérifier que le canal peut recevoir des messages
            if not hasattr(channel, 'send'):
                logger.warning(f"Canal {monitor_config['channel_id']} ne supporte pas l'envoi de messages")
                return

            sensor_name = sensor_info.get('name', f'ID {sensor_id}')
            
            # Convertir les statuts numériques en texte pour une meilleure lisibilité
            status_map = {
                0: 'down',
                1: 'up', 
                2: 'pending',
                9: 'maintenance'
            }
            
            # Convertir les statuts si nécessaire
            if isinstance(current_status, int):
                current_status = status_map.get(current_status, str(current_status))
            if isinstance(last_status, int):
                last_status = status_map.get(last_status, str(last_status))
            
            # Déterminer le type de notification
            if current_status == 'maintenance' or current_status == 9:
                embed = Embed(
                    title="🔧 Maintenance en cours",
                    description=f"Le capteur **{sensor_name}** est actuellement en maintenance.",
                    color=0xFFA500
                )
            elif (last_status == 'maintenance' or last_status == 9) and (current_status in ['up', 'online'] or current_status == 1):
                embed = Embed(
                    title="✅ Fin de maintenance",
                    description=f"Le capteur **{sensor_name}** est de nouveau opérationnel.",
                    color=0x00FF00
                )
            elif current_status in ['down', 'offline'] or current_status == 0:
                embed = Embed(
                    title="❌ Capteur hors ligne",
                    description=f"Le capteur **{sensor_name}** est actuellement hors ligne.",
                    color=0xFF0000
                )
            elif (current_status in ['up', 'online'] or current_status == 1) and (last_status in ['down', 'offline'] or last_status == 0):
                embed = Embed(
                    title="✅ Capteur en ligne",
                    description=f"Le capteur **{sensor_name}** est de nouveau en ligne.",
                    color=0x00FF00
                )
            elif current_status == 'pending' or current_status == 2:
                embed = Embed(
                    title="⏳ Capteur en attente",
                    description=f"Le capteur **{sensor_name}** est en cours de vérification.",
                    color=0xFFFF00
                )
            else:
                # Autres changements d'état
                embed = Embed(
                    title="ℹ️ Changement d'état",
                    description=f"Le capteur **{sensor_name}** a changé d'état: {last_status} → {current_status}",
                    color=0x0099FF
                )

            # Ajouter des informations supplémentaires
            embed.add_field(name="ID du capteur", value=sensor_id, inline=True)
            embed.add_field(name="État actuel", value=current_status, inline=True)
            
            # Ajouter des informations supplémentaires provenant de SocketIO
            if sensor_info.get('url'):
                embed.add_field(name="URL", value=sensor_info['url'], inline=False)
            if sensor_info.get('msg'):
                embed.add_field(name="Message", value=sensor_info['msg'], inline=False)
            if sensor_info.get('ping') is not None:
                embed.add_field(name="Ping", value=f"{sensor_info['ping']} ms", inline=True)

            # Utiliser getattr pour éviter les problèmes de types
            send_method = getattr(channel, 'send', None)
            if send_method:
                await send_method(embed=embed)
            else:
                logger.warning(f"Impossible d'envoyer un message dans le canal {channel}")

        except Exception as error:
            logger.error(f"Erreur lors de l'envoi de la notification: {error}")

    async def disconnect_socketio(self):
        """
        Ferme proprement la connexion SocketIO.
        """
        if self.sio and self.connected:
            try:
                await self.sio.disconnect()
                logger.info("Connexion SocketIO fermée proprement")
            except Exception as error:
                logger.error(f"Erreur lors de la fermeture SocketIO: {error}")
        self.connected = False

    @slash_command(
        name="socketio_status",
        description="Affiche l'état de la connexion SocketIO avec Uptime Kuma"
    )
    async def socketio_status(self, ctx: SlashContext):
        """
        Affiche l'état de la connexion SocketIO.
        """
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        # Vérifier si le module est activé sur ce serveur
        if str(ctx.guild.id) not in enabled_servers:
            await ctx.send("❌ Le module Uptime n'est pas activé sur ce serveur.", ephemeral=True)
            return

        embed = Embed(title="📡 État de la connexion SocketIO", color=0x0099FF)
        
        if self.connected and self.sio:
            embed.add_field(name="Statut", value="✅ Connecté", inline=True)
            embed.add_field(name="Monitors en cache", value=str(len(self.monitors_cache)), inline=True)
        else:
            embed.add_field(name="Statut", value="❌ Déconnecté", inline=True)
            embed.add_field(name="Monitors en cache", value="0", inline=True)
        
        embed.add_field(name="URL", value=config.get('uptimeKuma', {}).get('uptimeKumaUrl', 'Non configuré'), inline=False)
        embed.add_field(name="Monitors surveillés", value=str(sum(len(sensors) for sensors in self.maintenance_monitors.values())), inline=True)
        
        await ctx.send(embed=embed)

    @slash_command(
        name="show_recent_events",
        description="Affiche les événements SocketIO récents reçus (pour debugging)"
    )
    async def show_recent_events(self, ctx: SlashContext):
        """
        Affiche les derniers événements SocketIO reçus.
        """
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        # Vérifier si le module est activé sur ce serveur
        if str(ctx.guild.id) not in enabled_servers:
            await ctx.send("❌ Le module Uptime n'est pas activé sur ce serveur.", ephemeral=True)
            return

        if not self.recent_events:
            await ctx.send("❌ Aucun événement récent enregistré.", ephemeral=True)
            return

        embed = Embed(title="📜 Événements SocketIO récents", color=0x0099FF)
        
        # Afficher les 10 derniers événements
        recent = self.recent_events[-10:] if len(self.recent_events) > 10 else self.recent_events
        
        for i, event in enumerate(recent, 1):
            import datetime
            timestamp = datetime.datetime.fromtimestamp(event['timestamp']).strftime('%H:%M:%S')
            embed.add_field(
                name=f"{i}. {event['event']} ({timestamp})",
                value=f"```{event['data'][:150]}{'...' if len(event['data']) > 150 else ''}```",
                inline=False
            )
        
        embed.set_footer(text=f"Total événements enregistrés: {len(self.recent_events)}")
        await ctx.send(embed=embed, ephemeral=True)

    @slash_command(
        name="test_socketio_events",
        description="Teste les événements SocketIO et affiche les informations de débogage"
    )
    async def test_socketio_events(self, ctx: SlashContext):
        """
        Teste les événements SocketIO.
        """
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        # Vérifier si le module est activé sur ce serveur
        if str(ctx.guild.id) not in enabled_servers:
            await ctx.send("❌ Le module Uptime n'est pas activé sur ce serveur.", ephemeral=True)
            return

        if not self.connected or not self.sio:
            await ctx.send("❌ SocketIO n'est pas connecté.", ephemeral=True)
            return

        await ctx.send("🔍 Test des événements SocketIO en cours...", ephemeral=True)
        
        try:
            # Forcer la mise à jour de la liste des moniteurs
            await self.sio.emit('getMonitorList')
            
            # Essayer de récupérer les détails de tous les moniteurs surveillés
            guild_id = str(ctx.guild.id)
            if guild_id in self.maintenance_monitors:
                for monitor_id in self.maintenance_monitors[guild_id].keys():
                    logger.info(f"Test: Demande des détails du moniteur {monitor_id}")
                    await self.sio.emit('getMonitor', int(monitor_id))
                    
            await ctx.send("✅ Événements de test envoyés! Vérifiez les logs pour voir les réponses.", ephemeral=True)
            
        except Exception as error:
            logger.error(f"Erreur lors du test des événements SocketIO: {error}")
            await ctx.send(f"❌ Erreur lors du test: {error}", ephemeral=True)

    @slash_command(
        name="reconnect_socketio",
        description="Force la reconnexion à Uptime Kuma via SocketIO"
    )
    async def reconnect_socketio(self, ctx: SlashContext):
        """
        Force la reconnexion SocketIO.
        """
        if not ctx.guild:
            await ctx.send("❌ Cette commande ne peut être utilisée que dans un serveur.", ephemeral=True)
            return
            
        # Vérifier si le module est activé sur ce serveur
        if str(ctx.guild.id) not in enabled_servers:
            await ctx.send("❌ Le module Uptime n'est pas activé sur ce serveur.", ephemeral=True)
            return

        await ctx.send("🔄 Tentative de reconnexion SocketIO...", ephemeral=True)
        
        try:
            # Fermer la connexion existante si elle existe
            await self.disconnect_socketio()
            
            # Se reconnecter
            await self.connect_socketio()
            
            if self.connected:
                await ctx.send("✅ Reconnexion SocketIO réussie!", ephemeral=True)
            else:
                await ctx.send("❌ Échec de la reconnexion SocketIO. Vérifiez les logs.", ephemeral=True)
                
        except Exception as error:
            logger.error(f"Erreur lors de la reconnexion SocketIO: {error}")
            await ctx.send(f"❌ Erreur lors de la reconnexion: {error}", ephemeral=True)

    @Task.create(IntervalTrigger(seconds=55))
    async def send_status_update(self):
        """
        Perform status checks and gather information about your service/script's status.
        """
        async with aiohttp.ClientSession() as session:
            try:
                # Create the URL
                url = f"https://{config['uptimeKuma']['uptimeKumaUrl']}/api/push/{config['uptimeKuma']['uptimeKumaToken']}?status=up&msg=OK&ping={round(self.bot.latency * 1000, 1)}"

                # Send the status update
                async with session.get(url) as response:
                    response.raise_for_status()
                    logger.debug("Status update sent successfully.")
            except aiohttp.ClientError as error:
                logger.error("Error sending status update: %s", error)


def setup(bot):
    """Setup function for loading the extension."""
    Uptime(bot)
