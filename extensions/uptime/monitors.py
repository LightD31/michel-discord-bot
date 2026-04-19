"""Resolve monitor lists / sensor info from SocketIO cache or REST fallback."""

import asyncio
import os

import aiohttp
from interactions import AutocompleteContext

from src import logutil

from ._common import config

logger = logutil.init_logger(os.path.basename(__file__))


class MonitorsMixin:
    """Fetch monitor metadata and power the sensor autocomplete."""

    async def _get_all_monitors(self):
        """Return ``{name: id}`` or ``{}`` on failure."""
        try:
            if self.connected and self.monitors_cache:
                monitors = {}
                for monitor_id, monitor_data in self.monitors_cache.items():
                    if isinstance(monitor_data, dict) and "name" in monitor_data:
                        monitors[monitor_data["name"]] = (
                            int(monitor_id) if monitor_id.isdigit() else monitor_id
                        )
                return monitors

            if self.connected and self.sio:
                await self.sio.emit("getMonitorList")
                await asyncio.sleep(0.5)

                if self.monitors_cache:
                    monitors = {}
                    for monitor_id, monitor_data in self.monitors_cache.items():
                        if isinstance(monitor_data, dict) and "name" in monitor_data:
                            monitors[monitor_data["name"]] = (
                                int(monitor_id) if monitor_id.isdigit() else monitor_id
                            )
                    return monitors

            if (
                config.get("uptimeKuma", {}).get("uptimeKumaUrl")
                and config.get("uptimeKuma", {}).get("uptimeKumaUsername")
                and config.get("uptimeKuma", {}).get("uptimeKumaPassword")
            ):
                async with aiohttp.ClientSession() as session:
                    auth = aiohttp.BasicAuth(
                        config.get("uptimeKuma", {}).get("uptimeKumaUsername", ""),
                        config.get("uptimeKuma", {}).get("uptimeKumaPassword", ""),
                    )
                    url = f"https://{config['uptimeKuma']['uptimeKumaUrl']}/api/monitors"

                    async with session.get(url, auth=auth) as response:
                        if response.status == 200:
                            data = await response.json()
                            monitors = {}
                            if isinstance(data, list):
                                for monitor in data:
                                    if "name" in monitor and "id" in monitor:
                                        monitors[monitor["name"]] = monitor["id"]
                            elif isinstance(data, dict):
                                for monitor_id, monitor_data in data.items():
                                    if isinstance(monitor_data, dict) and "name" in monitor_data:
                                        monitors[monitor_data["name"]] = (
                                            int(monitor_id) if monitor_id.isdigit() else monitor_id
                                        )
                            return monitors
                        else:
                            logger.warning(
                                f"Erreur API REST pour récupération des moniteurs: {response.status}"
                            )

        except Exception as error:
            logger.error(f"Erreur lors de la récupération des moniteurs: {error}")

        return {}

    async def _get_sensor_info(self, sensor_id: int):
        """Get a single sensor's metadata, preferring cache then SocketIO then REST."""
        sensor_id_str = str(sensor_id)

        if sensor_id_str in self.monitors_cache:
            return self.monitors_cache[sensor_id_str]

        if self.connected and self.sio:
            try:
                await self.sio.emit("getMonitor", sensor_id)
                await asyncio.sleep(0.5)

                if sensor_id_str in self.monitors_cache:
                    return self.monitors_cache[sensor_id_str]

            except Exception as error:
                logger.error(
                    f"Erreur lors de la récupération du moniteur {sensor_id} via SocketIO: {error}"
                )

        try:
            async with aiohttp.ClientSession() as session:
                auth = aiohttp.BasicAuth(
                    config.get("uptimeKuma", {}).get("uptimeKumaUsername", ""),
                    config.get("uptimeKuma", {}).get("uptimeKumaPassword", ""),
                )
                url = f"https://{config['uptimeKuma']['uptimeKumaUrl']}/api/monitor/{sensor_id}"

                async with session.get(url, auth=auth) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.monitors_cache[sensor_id_str] = data
                        return data
                    else:
                        logger.warning(
                            f"Erreur API REST pour capteur {sensor_id}: {response.status}"
                        )
                        return None
        except Exception as error:
            logger.error(
                f"Erreur lors de la récupération du capteur {sensor_id} via API REST: {error}"
            )
            return None

    async def sensor_autocomplete(self, ctx: AutocompleteContext):
        """Suggest matching sensor names from the full monitor list."""
        try:
            monitors = await self._get_all_monitors()
            if not monitors:
                await ctx.send(choices=[])
                return

            query = ctx.input_text.lower() if ctx.input_text else ""
            matching_monitors = []

            for name, monitor_id in monitors.items():
                if query in name.lower():
                    display_name = name[:97] + "..." if len(name) > 100 else name
                    matching_monitors.append({"name": display_name, "value": str(monitor_id)})

            await ctx.send(choices=matching_monitors[:25])

        except Exception as error:
            logger.error(f"Erreur dans l'autocomplétion des capteurs: {error}")
            await ctx.send(choices=[])
