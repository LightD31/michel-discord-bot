"""SocketIO connection, event handlers, and live monitor update dispatch."""

import os

from src import logutil

from ._common import config

logger = logutil.init_logger(os.path.basename(__file__))


class SocketIOMixin:
    """Maintain a SocketIO session with Uptime Kuma and stream status updates."""

    async def connect_socketio(self):
        """Establish the SocketIO session and wire up event handlers.

        Uptime Kuma's SocketIO auth uses username/password (plus optional 2FA),
        not an API key. See the official Uptime Kuma API documentation.
        """
        if not config.get("uptimeKuma", {}).get("uptimeKumaUrl"):
            logger.error("Configuration Uptime Kuma manquante pour SocketIO - URL requise")
            return

        if not config.get("uptimeKuma", {}).get("uptimeKumaUsername") or not config.get(
            "uptimeKuma", {}
        ).get("uptimeKumaPassword"):
            logger.error(
                "Configuration Uptime Kuma manquante pour SocketIO - username et password requis"
            )
            return

        try:
            import socketio

            self.sio = socketio.AsyncClient()

            @self.sio.event
            async def connect():
                logger.info("Connexion SocketIO établie avec Uptime Kuma")
                self.connected = True
                if self.sio:
                    try:
                        response = await self.sio.call(
                            "login",
                            {
                                "username": config.get("uptimeKuma", {}).get("uptimeKumaUsername"),
                                "password": config.get("uptimeKuma", {}).get("uptimeKumaPassword"),
                                "token": config.get("uptimeKuma", {}).get("uptimeKuma2FA", ""),
                            },
                        )

                        if response and response.get("ok"):
                            logger.info("Authentification SocketIO réussie")
                            await self.sio.emit("getMonitorList")
                            await self._subscribe_to_monitors()
                        else:
                            error_msg = (
                                response.get("msg", "Erreur inconnue")
                                if response
                                else "Aucune réponse reçue"
                            )
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
                await self.handle_monitor_update(data)

            @self.sio.event
            async def heartbeat(data):
                """Primary real-time event used by the official Uptime Kuma frontend."""
                await self.handle_monitor_update(data)

            @self.sio.event
            async def monitorList(data):
                self.monitors_cache = data
                logger.info(f"Cache des moniteurs mis à jour: {len(data)} moniteurs")

            @self.sio.event
            async def loginRequired():
                logger.debug("Authentification requise par le serveur")

            @self.sio.event
            async def updateMonitorIntoList(data):
                for monitor_id, monitor_data in data.items():
                    self.monitors_cache[monitor_id] = monitor_data

            @self.sio.event
            async def info(data):
                pass

            @self.sio.event
            async def monitorBeat(data):
                await self.handle_monitor_update(data)

            @self.sio.event
            async def uptime(*args):
                pass

            @self.sio.event
            async def avgPing(*args):
                pass

            @self.sio.event
            async def heartbeatList(*args):
                """Real format: ``(monitor_id: str, heartbeats: list, important: bool)``."""
                if len(args) >= 2:
                    monitor_id_str = str(args[0])
                    heartbeats = args[1] if isinstance(args[1], list) else []
                    important = args[2] if len(args) > 2 else False

                    if heartbeats and len(heartbeats) > 0:
                        latest_heartbeat = heartbeats[-1]

                        monitor_update = {
                            "monitorID": int(monitor_id_str),
                            "status": latest_heartbeat.get("status"),
                            "msg": latest_heartbeat.get("msg"),
                            "ping": latest_heartbeat.get("ping"),
                            "time": latest_heartbeat.get("time"),
                            "important": important,
                        }

                        await self.handle_monitor_update(monitor_update)
                else:
                    logger.warning(
                        f"Format heartbeatList inattendu: {len(args)} arguments de types {[type(arg) for arg in args]}"
                    )

            @self.sio.event
            async def connect_error(data):
                logger.error(f"Erreur de connexion SocketIO: {data}")

            url = f"https://{config['uptimeKuma']['uptimeKumaUrl']}"
            await self.sio.connect(url, transports=["websocket", "polling"])

        except ImportError:
            logger.error("Module 'socketio' non disponible. Utilisez: pip install python-socketio")
        except Exception as error:
            logger.error(f"Erreur lors de la connexion SocketIO: {error}")

    async def _subscribe_to_monitors(self):
        """Ping each tracked monitor to confirm existence and prime the cache."""
        try:
            if not self.connected or not self.sio:
                logger.warning("Pas de connexion SocketIO active pour s'abonner aux moniteurs")
                return

            monitored_ids = set()
            for guild_monitors in self.maintenance_monitors.values():
                monitored_ids.update(guild_monitors.keys())

            if monitored_ids:
                logger.info(f"Abonnement aux moniteurs: {monitored_ids}")
                for monitor_id in monitored_ids:
                    try:
                        await self.sio.emit("getMonitor", int(monitor_id))
                    except Exception as e:
                        logger.debug(
                            f"Erreur lors de la souscription au moniteur {monitor_id}: {e}"
                        )
            else:
                logger.info("Aucun moniteur à surveiller configuré")

        except Exception as error:
            logger.error(f"Erreur lors de l'abonnement aux moniteurs: {error}")

    async def handle_monitor_update(self, data):
        """Dispatch ``heartbeat`` / ``monitor`` / ``monitorBeat`` payloads."""
        try:
            monitor_id = None
            status = None

            if "monitorID" in data:
                monitor_id = str(data.get("monitorID"))
                status = data.get("status")
            elif "id" in data:
                monitor_id = str(data.get("id"))
                status = data.get("status")

            if not monitor_id:
                return

            if monitor_id in self.monitors_cache:
                if isinstance(self.monitors_cache[monitor_id], dict):
                    self.monitors_cache[monitor_id].update(data)
                else:
                    self.monitors_cache[monitor_id] = data
            else:
                self.monitors_cache[monitor_id] = data

            for guild_id, sensors in self.maintenance_monitors.items():
                if monitor_id in sensors:
                    monitor_config = sensors[monitor_id]
                    last_status = monitor_config.get("last_status")

                    if last_status != status and status is not None:
                        logger.info(
                            f"Changement d'état détecté pour moniteur {monitor_id}: {last_status} → {status}"
                        )
                        monitor_info = self.monitors_cache.get(monitor_id, data)

                        if "mode" not in monitor_config:
                            monitor_config["mode"] = "detailed"

                        await self._send_maintenance_notification(
                            guild_id, monitor_id, monitor_info, status, last_status, monitor_config
                        )

                        self.maintenance_monitors[guild_id][monitor_id]["last_status"] = status
                        await self.save_maintenance_monitors()

        except Exception as error:
            logger.error(f"Erreur lors du traitement de la mise à jour du moniteur: {error}")

    async def disconnect_socketio(self):
        if self.sio and self.connected:
            try:
                await self.sio.disconnect()
                logger.info("Connexion SocketIO fermée proprement")
            except Exception as error:
                logger.error(f"Erreur lors de la fermeture SocketIO: {error}")
        self.connected = False
