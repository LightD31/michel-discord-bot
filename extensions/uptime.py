"""
This module provides a Discord bot extension that sends a status update to a server at a specific time
and monitors specific sensors for maintenance notifications.
"""

import os

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
    BaseChannel,
    AutocompleteContext,
    slash_default_member_permission,
    Permissions
)

from src import logutil
from src.mongodb import mongo_manager
from src.config_manager import load_config
from src.helpers import Colors, send_error, is_guild_enabled

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleUptime")

class UptimeExtension(Extension):
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
                await self.handle_monitor_update(data)

            @self.sio.event
            async def heartbeat(data):
                """
                Événement heartbeat - PRINCIPAL pour les mises à jour temps réel.
                Cet événement est utilisé par le frontend officiel d'Uptime Kuma.
                """
                await self.handle_monitor_update(data)

            @self.sio.event
            async def monitorList(data):
                """
                Reçoit la liste des moniteurs.
                """
                self.monitors_cache = data
                logger.info(f"Cache des moniteurs mis à jour: {len(data)} moniteurs")
                
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
                for monitor_id, monitor_data in data.items():
                    self.monitors_cache[monitor_id] = monitor_data

            # Ajouter d'autres événements pour le débogage
            @self.sio.event
            async def info(data):
                """
                Événement d'information.
                """
                pass

            @self.sio.event
            async def monitorBeat(data):
                """
                Événement de battement de moniteur (peut être utilisé au lieu de 'monitor').
                """
                await self.handle_monitor_update(data)

            @self.sio.event
            async def uptime(*args):
                """
                Événement uptime - statistiques d'uptime.
                """
                # Ces événements ne nécessitent pas de traitement spécial
                pass

            @self.sio.event
            async def avgPing(*args):
                """
                Événement avgPing - ping moyen.
                """
                # Ces événements ne nécessitent pas de traitement spécial
                pass

            @self.sio.event
            async def heartbeatList(*args):
                """
                Événement heartbeatList - liste des heartbeats avec données d'uptime.
                Format réel: (monitor_id: str, heartbeats: list, important: bool)
                """
                # Format réel identifié : 3 arguments (monitor_id, heartbeats, important)
                if len(args) >= 2:
                    monitor_id_str = str(args[0])
                    heartbeats = args[1] if isinstance(args[1], list) else []
                    important = args[2] if len(args) > 2 else False
                    
                    # Traiter les heartbeats s'il y en a
                    if heartbeats and len(heartbeats) > 0:
                        # Le heartbeat le plus récent est généralement le dernier dans la liste
                        latest_heartbeat = heartbeats[-1]  # Dernier = plus récent
                        
                        # Extraire les informations du heartbeat
                        status = latest_heartbeat.get('status')
                        msg = latest_heartbeat.get('msg')
                        ping = latest_heartbeat.get('ping')
                        time = latest_heartbeat.get('time')
                        
                        # Traiter comme une mise à jour de moniteur
                        monitor_update = {
                            'monitorID': int(monitor_id_str),
                            'status': status,
                            'msg': msg,
                            'ping': ping,
                            'time': time,
                            'important': important  # Inclure le paramètre important
                        }
                        
                        await self.handle_monitor_update(monitor_update)
                        
                else:
                    logger.warning(f"Format heartbeatList inattendu: {len(args)} arguments de types {[type(arg) for arg in args]}")

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
                return
            
            # Mettre à jour le cache avec plus d'informations
            if monitor_id in self.monitors_cache:
                # Fusionner les nouvelles données avec les existantes
                if isinstance(self.monitors_cache[monitor_id], dict):
                    self.monitors_cache[monitor_id].update(data)
                else:
                    self.monitors_cache[monitor_id] = data
            else:
                self.monitors_cache[monitor_id] = data
            
            # Vérifier si ce moniteur est surveillé
            for guild_id, sensors in self.maintenance_monitors.items():
                if monitor_id in sensors:
                    monitor_config = sensors[monitor_id]
                    last_status = monitor_config.get('last_status')
                    
                    if last_status != status and status is not None:
                        logger.info(f"Changement d'état détecté pour moniteur {monitor_id}: {last_status} → {status}")
                        # Récupérer les infos complètes du moniteur
                        monitor_info = self.monitors_cache.get(monitor_id, data)
                        
                        # S'assurer que monitor_config a le mode défini (compatibilité)
                        if 'mode' not in monitor_config:
                            monitor_config['mode'] = 'detailed'
                        
                        await self._send_maintenance_notification(
                            guild_id, monitor_id, monitor_info, status, last_status, monitor_config
                        )
                        
                        # Mettre à jour le dernier état connu
                        self.maintenance_monitors[guild_id][monitor_id]['last_status'] = status
                        await self.save_maintenance_monitors()
                        
        except Exception as error:
            logger.error(f"Erreur lors du traitement de la mise à jour du moniteur: {error}")

    async def _get_all_monitors(self):
        """
        Récupère tous les moniteurs disponibles depuis Uptime Kuma.
        Retourne un dictionnaire {nom: id} ou None en cas d'erreur.
        """
        try:
            # Si on a une connexion SocketIO active, utiliser le cache
            if self.connected and self.monitors_cache:
                monitors = {}
                for monitor_id, monitor_data in self.monitors_cache.items():
                    if isinstance(monitor_data, dict) and 'name' in monitor_data:
                        monitors[monitor_data['name']] = int(monitor_id) if monitor_id.isdigit() else monitor_id
                return monitors
            
            # Sinon, faire une requête SocketIO pour obtenir la liste
            if self.connected and self.sio:
                await self.sio.emit('getMonitorList')
                # Attendre un court délai pour recevoir la réponse
                import asyncio
                await asyncio.sleep(0.5)
                
                if self.monitors_cache:
                    monitors = {}
                    for monitor_id, monitor_data in self.monitors_cache.items():
                        if isinstance(monitor_data, dict) and 'name' in monitor_data:
                            monitors[monitor_data['name']] = int(monitor_id) if monitor_id.isdigit() else monitor_id
                    return monitors
            
            # Fallback vers l'API REST
            if (config.get('uptimeKuma', {}).get('uptimeKumaUrl') and 
                config.get('uptimeKuma', {}).get('uptimeKumaUsername') and 
                config.get('uptimeKuma', {}).get('uptimeKumaPassword')):
                
                async with aiohttp.ClientSession() as session:
                    auth = aiohttp.BasicAuth(
                        config.get('uptimeKuma', {}).get('uptimeKumaUsername', ''),
                        config.get('uptimeKuma', {}).get('uptimeKumaPassword', '')
                    )
                    url = f"https://{config['uptimeKuma']['uptimeKumaUrl']}/api/monitors"
                    
                    async with session.get(url, auth=auth) as response:
                        if response.status == 200:
                            data = await response.json()
                            monitors = {}
                            if isinstance(data, list):
                                for monitor in data:
                                    if 'name' in monitor and 'id' in monitor:
                                        monitors[monitor['name']] = monitor['id']
                            elif isinstance(data, dict):
                                for monitor_id, monitor_data in data.items():
                                    if isinstance(monitor_data, dict) and 'name' in monitor_data:
                                        monitors[monitor_data['name']] = int(monitor_id) if monitor_id.isdigit() else monitor_id
                            return monitors
                        else:
                            logger.warning(f"Erreur API REST pour récupération des moniteurs: {response.status}")
                            
        except Exception as error:
            logger.error(f"Erreur lors de la récupération des moniteurs: {error}")
        
        return {}

    async def sensor_autocomplete(self, ctx: AutocompleteContext):
        """
        Fonction d'autocomplétion pour les noms de capteurs.
        """
        try:
            monitors = await self._get_all_monitors()
            if not monitors:
                await ctx.send(choices=[])
                return
            
            # Filtrer les résultats selon ce que l'utilisateur tape
            query = ctx.input_text.lower() if ctx.input_text else ""
            matching_monitors = []
            
            for name, monitor_id in monitors.items():
                if query in name.lower():
                    # Limiter le nom à 100 caractères pour éviter les erreurs Discord
                    display_name = name[:97] + "..." if len(name) > 100 else name
                    matching_monitors.append({"name": display_name, "value": str(monitor_id)})
            
            # Limiter à 25 résultats maximum (limite Discord)
            await ctx.send(choices=matching_monitors[:25])
            
        except Exception as error:
            logger.error(f"Erreur dans l'autocomplétion des capteurs: {error}")
            await ctx.send(choices=[])

    @slash_command(
        name="uptime",
        description="Les commandes de surveillance Uptime Kuma"
    )
    @slash_default_member_permission(Permissions.ADMINISTRATOR)
    async def uptime_command(self, ctx: SlashContext) -> None:
        pass

    @uptime_command.subcommand(
        sub_cmd_name="setup",
        sub_cmd_description="Configure les alertes de maintenance pour un capteur spécifique"
    )
    @slash_option(
        name="sensor",
        description="Nom du capteur à surveiller",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True
    )
    @slash_option(
        name="channel",
        description="Canal où envoyer les notifications",
        opt_type=OptionType.CHANNEL,
        required=True
    )
    @slash_option(
        name="mode",
        description="Mode d'affichage des notifications",
        opt_type=OptionType.STRING,
        required=False,
        choices=[
            {"name": "Simple (titre et statut seulement)", "value": "simple"},
            {"name": "Détaillé (avec toutes les informations)", "value": "detailed"}
        ]
    )
    async def setup_maintenance_alert(self, ctx: SlashContext, sensor: str, channel: BaseChannel, mode: str = "detailed"):
        """
        Configure une alerte de maintenance pour un capteur spécifique dans un canal donné.
        """
        if not ctx.guild:
            await send_error(ctx, "Cette commande ne peut être utilisée que dans un serveur.")
            return

        # Vérifier si le module est activé sur ce serveur
        if not is_guild_enabled(ctx.guild.id, enabled_servers):
            await send_error(ctx, "Le module Uptime n'est pas activé sur ce serveur.")
            return
            
        guild_id = str(ctx.guild.id)
        
        # Vérifier que l'API Uptime Kuma est configurée
        if (not config.get('uptimeKuma', {}).get('uptimeKumaUrl') or 
            not config.get('uptimeKuma', {}).get('uptimeKumaUsername') or 
            not config.get('uptimeKuma', {}).get('uptimeKumaPassword')):
            await send_error(ctx, "Configuration Uptime Kuma manquante. Vérifiez l'URL, le nom d'utilisateur et le mot de passe.")
            return

        # Convertir le sensor (ID sous forme de string) en int
        try:
            sensor_id = int(sensor)
        except ValueError:
            await send_error(ctx, "ID de capteur invalide.")
            return

        # Vérifier si le capteur existe
        sensor_info = await self._get_sensor_info(sensor_id)
        if not sensor_info:
            await send_error(ctx, f"Capteur avec l'ID {sensor_id} introuvable.")
            return

        # Initialiser la structure si nécessaire
        if guild_id not in self.maintenance_monitors:
            self.maintenance_monitors[guild_id] = {}

        # Configurer la surveillance
        self.maintenance_monitors[guild_id][str(sensor_id)] = {
            "channel_id": channel.id,
            "last_status": None,
            "mode": mode
        }

        # Sauvegarder la configuration
        await self.save_maintenance_monitors()

        embed = Embed(
            title="✅ Alerte de maintenance configurée",
            description=f"Les notifications de maintenance pour le capteur **{sensor_info.get('name', f'ID {sensor_id}')}** seront envoyées dans {channel.mention} (Mode: {mode})",
            color=Colors.SUCCESS
        )
        await ctx.send(embed=embed)

    @setup_maintenance_alert.autocomplete("sensor")
    async def setup_sensor_autocomplete(self, ctx: AutocompleteContext):
        """Autocomplétion pour le paramètre sensor de la commande setup."""
        await self.sensor_autocomplete(ctx)

    @uptime_command.subcommand(
        sub_cmd_name="remove",
        sub_cmd_description="Supprime les alertes de maintenance pour un capteur"
    )
    @slash_option(
        name="sensor",
        description="Nom du capteur à ne plus surveiller",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True
    )
    async def remove_maintenance_alert(self, ctx: SlashContext, sensor: str):
        """
        Supprime la surveillance de maintenance pour un capteur spécifique.
        """
        if not ctx.guild:
            await send_error(ctx, "Cette commande ne peut être utilisée que dans un serveur.")
            return

        # Vérifier si le module est activé sur ce serveur
        if not is_guild_enabled(ctx.guild.id, enabled_servers):
            await send_error(ctx, "Le module Uptime n'est pas activé sur ce serveur.")
            return
            
        guild_id = str(ctx.guild.id)
        
        # Convertir le sensor (ID sous forme de string) en int puis en string pour la clé
        try:
            sensor_id = int(sensor)
            sensor_id_str = str(sensor_id)
        except ValueError:
            await send_error(ctx, "ID de capteur invalide.")
            return

        if (guild_id not in self.maintenance_monitors or 
            sensor_id_str not in self.maintenance_monitors[guild_id]):
            await send_error(ctx, f"Aucune alerte configurée pour le capteur ID {sensor_id}.")
            return

        del self.maintenance_monitors[guild_id][sensor_id_str]
        
        # Nettoyer si plus de surveillance pour ce serveur
        if not self.maintenance_monitors[guild_id]:
            del self.maintenance_monitors[guild_id]

        # Sauvegarder la configuration
        await self.save_maintenance_monitors()

        await ctx.send(f"✅ Alerte de maintenance supprimée pour le capteur ID {sensor_id}.")

    @remove_maintenance_alert.autocomplete("sensor")
    async def remove_sensor_autocomplete(self, ctx: AutocompleteContext):
        """Autocomplétion pour le paramètre sensor de la commande remove - ne montre que les capteurs surveillés."""
        try:
            if not ctx.guild:
                await ctx.send(choices=[])
                return
                
            guild_id = str(ctx.guild.id)
            
            # Récupérer seulement les capteurs surveillés sur ce serveur
            if guild_id not in self.maintenance_monitors:
                await ctx.send(choices=[])
                return
            
            monitored_sensors = []
            query = ctx.input_text.lower() if ctx.input_text else ""
            
            for sensor_id_str in self.maintenance_monitors[guild_id].keys():
                try:
                    sensor_id = int(sensor_id_str)
                    sensor_info = await self._get_sensor_info(sensor_id)
                    
                    if sensor_info and 'name' in sensor_info:
                        sensor_name = sensor_info['name']
                        if query in sensor_name.lower():
                            # Limiter le nom à 100 caractères pour éviter les erreurs Discord
                            display_name = sensor_name[:97] + "..." if len(sensor_name) > 100 else sensor_name
                            monitored_sensors.append({"name": display_name, "value": sensor_id_str})
                except (ValueError, TypeError):
                    continue
            
            await ctx.send(choices=monitored_sensors[:25])
            
        except Exception as error:
            logger.error(f"Erreur dans l'autocomplétion des capteurs surveillés: {error}")
            await ctx.send(choices=[])

    @uptime_command.subcommand(
        sub_cmd_name="list",
        sub_cmd_description="Liste toutes les alertes de maintenance configurées"
    )
    async def list_maintenance_alerts(self, ctx: SlashContext):
        """
        Liste toutes les alertes de maintenance configurées pour ce serveur.
        """
        if not ctx.guild:
            await send_error(ctx, "Cette commande ne peut être utilisée que dans un serveur.")
            return

        # Vérifier si le module est activé sur ce serveur
        if not is_guild_enabled(ctx.guild.id, enabled_servers):
            await send_error(ctx, "Le module Uptime n'est pas activé sur ce serveur.")
            return
            
        guild_id = str(ctx.guild.id)

        if guild_id not in self.maintenance_monitors or not self.maintenance_monitors[guild_id]:
            await send_error(ctx, "Aucune alerte de maintenance configurée sur ce serveur.")
            return

        embed = Embed(
            title="📋 Alertes de maintenance configurées",
            color=Colors.INFO
        )

        for sensor_id, config_data in self.maintenance_monitors[guild_id].items():
            channel = self.bot.get_channel(config_data["channel_id"])
            sensor_info = await self._get_sensor_info(int(sensor_id))
            sensor_name = sensor_info.get('name', f'ID {sensor_id}') if sensor_info else f'ID {sensor_id}'
            mode = config_data.get('mode', 'detailed')
            
            embed.add_field(
                name=f"Capteur: {sensor_name}",
                value=f"Canal: {channel.mention if channel else 'Canal introuvable'}\nMode: {mode}",
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
                        # S'assurer que monitor_config a le mode défini (compatibilité)
                        if 'mode' not in monitor_config:
                            monitor_config['mode'] = 'detailed'
                            
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
        Charge les configurations de surveillance depuis MongoDB (per-guild DB).
        """
        try:
            self.maintenance_monitors = {}
            for guild_id in enabled_servers:
                col = mongo_manager.get_guild_collection(guild_id, "uptime_monitors")
                doc = await col.find_one({"_id": "config"})
                if doc:
                    self.maintenance_monitors[guild_id] = {k: v for k, v in doc.items() if k != "_id"}
            logger.info(f"Configurations de surveillance chargées: {len(self.maintenance_monitors)} serveurs")
        except Exception as error:
            logger.error(f"Erreur lors du chargement des configurations: {error}")
            self.maintenance_monitors = {}

    async def save_maintenance_monitors(self):
        """
        Sauvegarde les configurations de surveillance dans MongoDB (per-guild DB).
        """
        try:
            for guild_id, monitors in self.maintenance_monitors.items():
                col = mongo_manager.get_guild_collection(guild_id, "uptime_monitors")
                await col.update_one(
                    {"_id": "config"}, {"$set": monitors}, upsert=True
                )
        except Exception as error:
            logger.error(f"Erreur lors de la sauvegarde des configurations: {error}")

    async def _send_maintenance_notification(self, guild_id: str, sensor_id: str, sensor_info: dict, 
                                           current_status: str, last_status: str, monitor_config: dict):
        """
        Envoie une notification de maintenance dans le canal configuré.
        Gère les statuts de l'API SocketIO (0=DOWN, 1=UP, 2=PENDING, 3=MAINTENANCE).
        Envoie seulement les notifications si important=True et ignore les statuts PENDING.
        Supporte deux modes: simple et détaillé.
        """
        try:
            # Vérifier si l'événement est marqué comme important
            is_important = sensor_info.get('important', False)
            if not is_important:
                logger.debug(f"Événement non important ignoré pour moniteur {sensor_id}: status={current_status}")
                return

            channel = self.bot.get_channel(monitor_config['channel_id'])
            if not channel:
                logger.warning(f"Canal {monitor_config['channel_id']} introuvable pour les notifications")
                return

            # Vérifier que le canal peut recevoir des messages
            if not hasattr(channel, 'send'):
                logger.warning(f"Canal {monitor_config['channel_id']} ne supporte pas l'envoi de messages")
                return

            sensor_name = sensor_info.get('name', f'ID {sensor_id}')
            notification_mode = monitor_config.get('mode', 'detailed')
            
            # Convertir les statuts numériques selon la nouvelle spécification
            # 0=DOWN, 1=UP, 2=PENDING, 3=MAINTENANCE
            status_map = {
                0: 'DOWN',
                1: 'UP', 
                2: 'PENDING',
                3: 'MAINTENANCE'
            }
            
            # Convertir les statuts si nécessaire
            if isinstance(current_status, int):
                current_status = status_map.get(current_status, str(current_status))
            if isinstance(last_status, int):
                last_status = status_map.get(last_status, str(last_status))
            
            # Ignorer les statuts PENDING
            if current_status == 'PENDING' or current_status == 2:
                logger.debug(f"Statut PENDING ignoré pour moniteur {sensor_id}")
                return
            
            # Créer des embeds spécifiques selon le statut
            embed = None
            
            if current_status == 'MAINTENANCE' or current_status == 3:
                if notification_mode == "simple":
                    embed = Embed(
                        title="🔧 Maintenance",
                        description=f"**{sensor_name}** est en maintenance",
                        color=Colors.ORANGE  # Orange
                    )
                else:  # detailed
                    embed = Embed(
                        title="🔧 Maintenance en cours",
                        description=f"Le capteur **{sensor_name}** est actuellement en maintenance.",
                        color=Colors.ORANGE  # Orange
                    )
            elif current_status == 'DOWN' or current_status == 0:
                if notification_mode == "simple":
                    embed = Embed(
                        title="❌ Hors ligne",
                        description=f"**{sensor_name}** est hors ligne",
                        color=Colors.ERROR  # Rouge
                    )
                else:  # detailed
                    embed = Embed(
                        title="❌ Capteur hors ligne",
                        description=f"Le capteur **{sensor_name}** est actuellement hors ligne.",
                        color=Colors.ERROR  # Rouge
                    )
            elif current_status == 'UP' or current_status == 1:
                # Différencier selon l'état précédent
                if last_status in ['DOWN', 'MAINTENANCE', 0, 3]:
                    if last_status in ['MAINTENANCE', 3]:
                        if notification_mode == "simple":
                            embed = Embed(
                                title="✅ Maintenance terminée",
                                description=f"**{sensor_name}** opérationnel",
                                color=Colors.SUCCESS  # Vert
                            )
                        else:  # detailed
                            embed = Embed(
                                title="✅ Fin de maintenance",
                                description=f"Le capteur **{sensor_name}** est de nouveau opérationnel après maintenance.",
                                color=Colors.SUCCESS  # Vert
                            )
                    else:
                        if notification_mode == "simple":
                            embed = Embed(
                                title="✅ Rétabli",
                                description=f"**{sensor_name}** est en ligne",
                                color=Colors.SUCCESS  # Vert
                            )
                        else:  # detailed
                            embed = Embed(
                                title="✅ Capteur rétabli",
                                description=f"Le capteur **{sensor_name}** est de nouveau en ligne.",
                                color=Colors.SUCCESS  # Vert
                            )
                else:
                    # Statut UP mais sans changement significatif, ignorer
                    logger.debug(f"Changement d'état UP non significatif ignoré pour moniteur {sensor_id}")
                    return
            
            # Si aucun embed n'a été créé, ne rien envoyer
            if not embed:
                logger.debug(f"Aucun embed créé pour moniteur {sensor_id}: {last_status} → {current_status}")
                return

            # Mode détaillé : ajouter des informations supplémentaires
            if notification_mode == "detailed":
                embed.add_field(name="ID du capteur", value=sensor_id, inline=True)
                embed.add_field(name="État actuel", value=current_status, inline=True)
                
                # Ajouter l'état précédent si pertinent
                if last_status and last_status != current_status:
                    embed.add_field(name="État précédent", value=last_status, inline=True)
                
                # Ajouter des informations supplémentaires provenant de SocketIO
                if sensor_info.get('url'):
                    embed.add_field(name="URL", value=sensor_info['url'], inline=False)
                if sensor_info.get('msg'):
                    embed.add_field(name="Message", value=sensor_info['msg'], inline=False)
                if sensor_info.get('ping') is not None:
                    embed.add_field(name="Ping", value=f"{sensor_info['ping']} ms", inline=True)

                # Ajouter un timestamp
                from interactions import Timestamp
                embed.timestamp = Timestamp.now()
            else:  # Mode simple
                # En mode simple, on ajoute seulement le statut actuel comme petit champ
                embed.add_field(name="Statut", value=current_status, inline=True)

            # Utiliser getattr pour éviter les problèmes de types
            send_method = getattr(channel, 'send', None)
            if send_method:
                await send_method(embed=embed)
                logger.info(f"Notification envoyée pour moniteur {sensor_id}: {last_status} → {current_status} (mode: {notification_mode})")
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
            except aiohttp.ClientError as error:
                logger.error("Error sending status update: %s", error)
