import json
import os
import pytz
import random
from datetime import datetime, timedelta
from aiohttp import ClientSession
from interactions import (
    ActionRow,
    BaseChannel,
    Button,
    ButtonStyle,
    Extension,
    Embed,
    File,
    IntervalTrigger,
    Message,
    OptionType,
    SlashContext,
    Task,
    TimeTrigger,
    OrTrigger,
    User,
    Client,
    listen,
    slash_command,
    slash_option,
    SlashCommandChoice,
)
from interactions.api.events import Component
from src import logutil
from src.utils import load_config, fetch

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleColoc")

# Server specific module
module_config = module_config[enabled_servers[0]]

# Keep track of reminders
reminders = {}

# Keep track of zunivers events and hardcore season
previous_events_state = {}
previous_hardcore_season = None

NORMAL_REMINDERS = [
    "Tu n'as pas encore fait ton [/journa](https://discord.com/channels/138283154589876224/808432657838768168) normal aujourd'hui !",
    "Hé ! N'oublie pas ton [/journa](https://discord.com/channels/138283154589876224/808432657838768168) du jour !",
    "Petit rappel : ton [/journa](https://discord.com/channels/138283154589876224/808432657838768168) t'attend !",
    "Il est temps de faire ton [/journa](https://discord.com/channels/138283154589876224/808432657838768168) quotidien !",
    "Ton [/journa](https://discord.com/channels/138283154589876224/808432657838768168) du jour t'attend !",
    "Psst... Tu as pensé à ton [/journa](https://discord.com/channels/138283154589876224/808432657838768168) aujourd'hui ?",
    "Allez, c'est le moment de faire ton [/journa](https://discord.com/channels/138283154589876224/808432657838768168) !",
    "N'oublie pas de valider ta journée avec ton [/journa](https://discord.com/channels/138283154589876224/808432657838768168) !",
    "Ton [/journa](https://discord.com/channels/138283154589876224/808432657838768168) quotidien n'attend que toi !",
    "Rappel amical : il est temps de faire ton [/journa](https://discord.com/channels/138283154589876224/808432657838768168) !"
]

HARDCORE_REMINDERS = [
    "Tu n'as pas encore fait ton [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) hardcore aujourd'hui !",
    "Attention ! Ton [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) hardcore du jour n'est pas fait !",
    "Ne laisse pas passer ton [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) hardcore aujourd'hui !",
    "Rappel crucial : ton [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) hardcore t'attend !",
    "Dernier appel pour ton [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) hardcore du jour !",
    "URGENT : Ton [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) hardcore n'est pas fait !",
    "Mode hardcore activé ! N'oublie pas ton [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) !",
    "Ton aventure hardcore t'attend avec ton [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) !",
    "Pas de repos pour les braves ! Fais ton [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) hardcore !",
    "Le mode hardcore ne pardonne pas : fais ton [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) maintenant !"
]

ADVENT_CALENDAR_REMINDERS = [
    "🎄 Tu n'as pas encore ouvert ta case du [calendrier festif](https://zunivers.zerator.com/calendrier-festif/{username}) aujourd'hui !",
    "🎁 N'oublie pas d'ouvrir ta case du [calendrier festif](https://zunivers.zerator.com/calendrier-festif/{username}) !",
    "❄️ Une surprise t'attend dans le [calendrier festif](https://zunivers.zerator.com/calendrier-festif/{username}) !",
    "🌟 Psst... ta case du jour du [calendrier festif](https://zunivers.zerator.com/calendrier-festif/{username}) n'est pas ouverte !",
    "🎅 Le Père Noël attend que tu ouvres ta case du [calendrier festif](https://zunivers.zerator.com/calendrier-festif/{username}) !",
]



class ColocClass(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot

    async def download_image_as_file(self, image_url: str, filename: str = "image.webp"):
        """
        Télécharge une image depuis une URL et retourne un objet File pour Discord.
        """
        try:
            import io
            from interactions import File
            
            async with ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        file_obj = io.BytesIO(image_data)
                        return File(file=file_obj, file_name=filename)
        except Exception as e:
            logger.warning(f"Erreur lors du téléchargement de l'image: {e}")
        return None

    @listen()
    async def on_startup(self):
        self.journa.start()
        await self.load_reminders()
        self.check_reminders.start()
        self.corpo_recap.start()
        await self.load_events_state()
        self.zunivers_events_checker.start()

    @slash_command(name="fesse", description="Fesses", scopes=enabled_servers)
    async def fesse(self, ctx: SlashContext):
        await ctx.send(
            "https://media1.tenor.com/m/YIUbUoKi8ZcAAAAC/sesame-street-kermit-the-frog.gif"
        )

    @slash_command(
        name="massageducul",
        description="Massage du cul",
        scopes=enabled_servers,
    )
    async def massageducul(self, ctx: SlashContext):
        await ctx.send("https://media1.tenor.com/m/h6OvENNtJh0AAAAC/bebou.gif")

    @Task.create(TimeTrigger(22, utc=False))
    async def journa(self):
        channel: BaseChannel = await self.bot.fetch_channel(
            module_config["colocZuniversChannelId"]
        )
        paris_tz = pytz.timezone("Europe/Paris")
        message: Message = (await channel.history(limit=1).flatten())[0]
        logger.debug(
            "Checking if message %s was posted today (message timestamp: %s today: %s",
            message.id,
            message.created_at.astimezone(paris_tz).strftime("%Y-%m-%d %H:%M:%S %Z"),
            datetime.now(pytz.UTC)
            .astimezone(paris_tz)
            .strftime("%Y-%m-%d %H:%M:%S %Z"),
        )
        if (
            message.created_at.astimezone(paris_tz).date()
            == datetime.now(paris_tz).date()
        ):
            logger.info(
                "Channel already posted today, skipping (message date: %s today: %s)",
                message.created_at.astimezone(paris_tz).date(),
                datetime.now(paris_tz).date(),
            )
            return
        await channel.send(
            ":robot: <@&934560421912911882>, heureusement que les robots n'oublient pas ! :robot:"
        )

    # Zunivers API
    async def load_reminders(self):
        """
        Load reminders from a JSON file and populate the reminders dictionary.
        """
        try:
            with open(
                f"{config['misc']['dataFolder']}/journa.json", "r", encoding="utf-8"
            ) as file:
                reminders_data = json.load(file)
                for remind_time_str, reminder_data in reminders_data.items():
                    remind_time = datetime.strptime(
                        remind_time_str, "%Y-%m-%d %H:%M:%S"
                    )
                    # Assurer que les clés NORMAL et HARDCORE existent
                    reminders[remind_time] = {
                        "NORMAL": reminder_data.get("NORMAL", []),
                        "HARDCORE": reminder_data.get("HARDCORE", []),
                    }
        except FileNotFoundError:
            pass

    async def save_reminders(self):
        """
        Save reminders to a JSON file.
        """
        reminders_data = {
            remind_time.strftime("%Y-%m-%d %H:%M:%S"): {
                "NORMAL": reminder_types["NORMAL"],
                "HARDCORE": reminder_types["HARDCORE"],
            }
            for remind_time, reminder_types in reminders.items()
        }
        with open(
            f"{config['misc']['dataFolder']}/journa.json", "w", encoding="utf-8"
        ) as file:
            json.dump(reminders_data, file, ensure_ascii=False, indent=4)

    # Zunivers Events tracking
    async def load_events_state(self):
        """
        Load previous events state from a JSON file.
        """
        global previous_events_state, previous_hardcore_season
        try:
            with open(
                f"{config['misc']['dataFolder']}/zunivers_events.json", "r", encoding="utf-8"
            ) as file:
                data = json.load(file)
                previous_events_state = data.get("events", {})
                previous_hardcore_season = data.get("hardcore_season", None)
        except FileNotFoundError:
            previous_events_state = {}
            previous_hardcore_season = None

    async def save_events_state(self):
        """
        Save current events and hardcore season state to a JSON file.
        """
        data = {
            "events": previous_events_state,
            "hardcore_season": previous_hardcore_season
        }
        with open(
            f"{config['misc']['dataFolder']}/zunivers_events.json", "w", encoding="utf-8"
        ) as file:
            json.dump(data, file, ensure_ascii=False, indent=4)

    async def check_zunivers_events(self):
        """
        Check current Zunivers events and compare with previous state to detect changes.
        """
        channel = await self.bot.fetch_channel(module_config["colocZuniversChannelId"])
        
        for rule_set in ["NORMAL", "HARDCORE"]:
            try:
                # Récupérer les événements actuels
                current_events = await fetch(
                    "https://zunivers-api.zerator.com/public/event/current",
                    "json",
                    headers={"X-ZUnivers-RuleSetType": rule_set}
                )
                
                # État précédent pour ce rule_set
                previous_state = previous_events_state.get(rule_set, {})
                current_state = {}
                
                # Analyser les événements actuels
                for event in current_events:
                    event_id = event["id"]
                    event_name = event["name"]
                    is_active = event["isActive"]
                    
                    current_state[event_id] = {
                        "name": event_name,
                        "is_active": is_active,
                        "begin_date": event["beginDate"],
                        "end_date": event["endDate"]
                    }
                    
                    # Vérifier si c'est un nouvel événement ou un changement d'état
                    if event_id not in previous_state:
                        if is_active:
                            # Nouvel événement actif
                            embed, image_file = await self.create_event_embed(event, "start", rule_set)
                            if image_file:
                                await channel.send(embed=embed, file=image_file)
                            else:
                                await channel.send(embed=embed)
                            logger.info(f"Nouvel événement {rule_set} détecté: {event_name}")
                    else:
                        # Événement existant - vérifier changement d'état
                        previous_active = previous_state[event_id]["is_active"]
                        if previous_active != is_active:
                            if is_active:
                                # L'événement vient de commencer
                                embed, image_file = await self.create_event_embed(event, "start", rule_set)
                                if image_file:
                                    await channel.send(embed=embed, file=image_file)
                                else:
                                    await channel.send(embed=embed)
                                logger.info(f"Événement {rule_set} commencé: {event_name}")
                            else:
                                # L'événement vient de se terminer
                                embed, image_file = await self.create_event_embed(event, "end", rule_set)
                                if image_file:
                                    await channel.send(embed=embed, file=image_file)
                                else:
                                    await channel.send(embed=embed)
                                logger.info(f"Événement {rule_set} terminé: {event_name}")
                
                # Vérifier les événements qui ont disparu (terminés)
                for event_id, prev_event in previous_state.items():
                    if event_id not in current_state and prev_event["is_active"]:
                        # Événement qui était actif et a maintenant disparu
                        fake_event = {
                            "id": event_id,
                            "name": prev_event["name"],
                            "isActive": False,
                            "beginDate": prev_event["begin_date"],
                            "endDate": prev_event["end_date"]
                        }
                        embed, image_file = await self.create_event_embed(fake_event, "end", rule_set)
                        if image_file:
                            await channel.send(embed=embed, file=image_file)
                        else:
                            await channel.send(embed=embed)
                        logger.info(f"Événement {rule_set} terminé (disparu): {prev_event['name']}")
                
                # Mettre à jour l'état pour ce rule_set
                previous_events_state[rule_set] = current_state
                
            except Exception as e:
                logger.error(f"Erreur lors de la vérification des événements {rule_set}: {e}")
        
        # Vérifier la saison hardcore actuelle
        await self.check_hardcore_season(channel)
        
        # Sauvegarder le nouvel état
        await self.save_events_state()

    async def create_event_embed(self, event, event_type, rule_set):
        """
        Create a Discord embed for an event start or end.
        
        Args:
            event: Event data from the API
            event_type: "start" or "end"
            rule_set: "NORMAL" or "HARDCORE"
        
        Returns:
            tuple: (embed, image_file) where image_file can be None
        """
        if event_type == "start":
            color = 0x00FF00  # Vert pour début
            title = f"🎉 Nouvel événement /im {event['name']}"
            description = "Un nouvel événement vient de commencer !"
        else:  # end
            color = 0xFF0000  # Rouge pour fin
            title = f"⏰ Fin d'événement /im {event['name']}"
            description = "L'événement vient de se terminer."
        
        # Timezone de Paris
        paris_tz = pytz.timezone("Europe/Paris")
        
        embed = Embed(
            title=title,
            description=description,
            color=color
        )
        
        # Conversion des dates avec le timezone de Paris
        # Les dates de l'API sont en heure de Paris (pas UTC)
        begin_date = datetime.fromisoformat(event['beginDate'])
        end_date = datetime.fromisoformat(event['endDate'])
        
        # Si les dates n'ont pas de timezone, on assume qu'elles sont en heure de Paris
        if begin_date.tzinfo is None:
            begin_date = paris_tz.localize(begin_date)
        if end_date.tzinfo is None:
            end_date = paris_tz.localize(end_date)
        
        # Informations de base
        embed.add_field(
            name="📅 Période",
            value=f"Du <t:{int(begin_date.timestamp())}:F>\nAu <t:{int(end_date.timestamp())}:F>",
            inline=False
        )
        
        # Ajouter les items s'il y en a et si c'est le début
        if event_type == "start" and "items" in event and event["items"]:
            items_by_rarity = {}
            for item in event["items"]:
                rarity = item["rarity"]
                if rarity not in items_by_rarity:
                    items_by_rarity[rarity] = []
                items_by_rarity[rarity].append(item["name"])
            
            items_text = ""
            rarity_emojis = {
                1: "<:rarity1:1421467748860432507>",
                2: "<:rarity2:1421467800366612602>",
                3: "<:rarity3:1421467829139538113>",
                4: "<:rarity4:1421467859841847406>",
                5: "<:rarity5:1421467889961275402>"
            }
            for rarity in sorted(items_by_rarity.keys(), reverse=True):
                rarity_emoji = rarity_emojis.get(rarity, "⭐" * rarity)  # Fallback aux étoiles si rareté inconnue
                rarity_display = rarity_emoji * rarity  # Répéter l'emoji selon le niveau de rareté
                items_text += f"{rarity_display} {', '.join(items_by_rarity[rarity][:3])}"
                if len(items_by_rarity[rarity]) > 3:
                    items_text += f" (+{len(items_by_rarity[rarity]) - 3} autres)"
                items_text += "\n"
            
            embed.add_field(
                name="🎁 Items disponibles",
                value=items_text[:1024],  # Limiter à 1024 caractères
                inline=False
            )
        
        # Coût si disponible
        if "balanceCost" in event:
            embed.add_field(
                name="💰 Coût",
                value=f"{event['balanceCost']} <:eraMonnaie:1265266681291341855>",
                inline=True
            )
        
        # Image si disponible - télécharger comme fichier pour forcer l'interprétation
        image_file = None
        if "imageUrl" in event and event["imageUrl"]:
            # Vérifier si l'URL a une extension
            has_extension = any(event["imageUrl"].lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'])
            
            if not has_extension:
                # Télécharger l'image et l'attacher comme fichier
                image_file = await self.download_image_as_file(event["imageUrl"], "event_image.webp")
                if image_file:
                    # Utiliser l'attachment comme image
                    embed.set_image(url="attachment://event_image.webp")
                else:
                    # Fallback vers l'URL originale
                    embed.set_image(url=event["imageUrl"])
            else:
                embed.set_image(url=event["imageUrl"])
        
        return embed, image_file

    async def check_hardcore_season(self, channel):
        """
        Check current hardcore season and compare with previous state to detect changes.
        """
        global previous_hardcore_season
        
        try:
            # Récupérer la saison hardcore actuelle
            current_season = await fetch(
                "https://zunivers-api.zerator.com/public/hardcore/season/current",
                "json",
                headers={"X-ZUnivers-RuleSetType": "HARDCORE"}
            )
            
            # Vérifier s'il y a un changement
            if previous_hardcore_season is None and current_season is not None:
                # Nouvelle saison commencée
                embed = await self.create_season_embed(current_season, "start")
                await channel.send(embed=embed)
                logger.info(f"Nouvelle saison hardcore détectée: Saison {current_season['index']}")
                
            elif previous_hardcore_season is not None and current_season is None:
                # Saison terminée
                embed = await self.create_season_embed(previous_hardcore_season, "end")
                await channel.send(embed=embed)
                logger.info(f"Fin de saison hardcore détectée: Saison {previous_hardcore_season['index']}")
                
            elif (previous_hardcore_season is not None and current_season is not None and
                  previous_hardcore_season.get("id") != current_season.get("id")):
                # Changement de saison
                embed_end = await self.create_season_embed(previous_hardcore_season, "end")
                embed_start = await self.create_season_embed(current_season, "start")
                await channel.send(embeds=[embed_end, embed_start])
                logger.info(f"Changement de saison hardcore: Saison {previous_hardcore_season['index']} → Saison {current_season['index']}")
            
            # Mettre à jour l'état
            previous_hardcore_season = current_season
            
        except Exception as e:
            logger.error(f"Erreur lors de la vérification de la saison hardcore: {e}")

    async def create_season_embed(self, season, season_type):
        """
        Create a Discord embed for a hardcore season start or end.
        
        Args:
            season: Season data from the API
            season_type: "start" or "end"
        """
        # Timezone de Paris
        paris_tz = pytz.timezone("Europe/Paris")
        
        if season_type == "start":
            color = 0xFF4500  # Orange pour début de saison hardcore
            title = f"🔥 Nouvelle saison HARDCORE : Saison {season['index']}"
            description = "Une nouvelle saison hardcore vient de commencer !"
        else:  # end
            color = 0x8B0000  # Rouge foncé pour fin de saison
            title = f"💀 Fin de saison HARDCORE : Saison {season['index']}"
            description = "La saison hardcore vient de se terminer."
        
        embed = Embed(
            title=title,
            description=description,
            color=color
        )
        
        # Conversion des dates avec le timezone de Paris
        begin_date = datetime.fromisoformat(season['beginDate'])
        end_date = datetime.fromisoformat(season['endDate'])
        
        # Si les dates n'ont pas de timezone, on assume qu'elles sont en heure de Paris
        if begin_date.tzinfo is None:
            begin_date = paris_tz.localize(begin_date)
        if end_date.tzinfo is None:
            end_date = paris_tz.localize(end_date)
        
        # Informations de la saison
        embed.add_field(
            name="📅 Période de la saison",
            value=f"Du <t:{int(begin_date.timestamp())}:F>\nAu <t:{int(end_date.timestamp())}:F>",
            inline=False
        )
        
        # Ajouter l'image du logo hardcore
        embed.set_image(url="https://zunivers.zerator.com/assets/logo-hc.webp")
        
        if season_type == "start":
            embed.add_field(
                name="⚠️ Mode Hardcore",
                value="Attention ! En mode hardcore, un oubli de [/journa](https://discord.com/channels/138283154589876224/1263861962744270958) et on recommmence tout !",
                inline=False
            )
        
        return embed

    @slash_command(
        name="zunivers",
        sub_cmd_name="check",
        sub_cmd_description="Vérifie manuellement les événements Zunivers",
        description="Gère les événements Zunivers",
        scopes=enabled_servers,
    )
    async def zunivers_check(self, ctx: SlashContext):
        """Commande pour tester manuellement la vérification des événements."""
        await ctx.defer()
        try:
            await self.check_zunivers_events()
            await ctx.send("Vérification des événements Zunivers terminée ! 🎉", ephemeral=True)
        except Exception as e:
            await ctx.send(f"Erreur lors de la vérification: {e}", ephemeral=True)
            logger.error(f"Erreur lors de la vérification manuelle des événements: {e}")

    # Set reminder to /journa
    @slash_command(
        name="journa",
        sub_cmd_name="set",
        sub_cmd_description="Supprime un rappel pour /journa",
        description="Gère les rappels pour /journa",
        scopes=enabled_servers,
    )
    @slash_option(
        name="heure",
        description="Heure du rappel",
        opt_type=OptionType.INTEGER,
        required=True,
        min_value=0,
        max_value=23,
    )
    @slash_option(
        "minute",
        "Minute du rappel",
        OptionType.INTEGER,
        required=True,
        min_value=0,
        max_value=59,
    )
    @slash_option(
        "type",
        "Type de /journa",
        OptionType.STRING,
        required=True,
        choices=[
            SlashCommandChoice(name="Normal", value="NORMAL"),
            SlashCommandChoice(name="Hardcore", value="HARDCORE"),
            SlashCommandChoice(name="Les deux", value="BOTH"),
        ],
    )
    async def rappelvote_set(
        self, ctx: SlashContext, heure: int, minute: int, type: str
    ):
        remind_time = datetime.now().replace(
            hour=heure, minute=minute, second=0, microsecond=0
        )
        if remind_time <= datetime.now():
            remind_time += timedelta(days=1)

        user_id = str(ctx.author.id)

        # Si BOTH, créer deux entrées séparées
        if type == "BOTH":
            if remind_time not in reminders:
                reminders[remind_time] = {"NORMAL": [], "HARDCORE": []}
            reminders[remind_time]["NORMAL"].append(user_id)
            reminders[remind_time]["HARDCORE"].append(user_id)
        else:
            if remind_time not in reminders:
                reminders[remind_time] = {"NORMAL": [], "HARDCORE": []}
            reminders[remind_time][type].append(user_id)

        await ctx.send(
            f"Rappel ajouté à {remind_time.strftime('%H:%M')}", ephemeral=True
        )
        logger.info(
            "Rappel %s à %s ajouté pour %s",
            type,
            remind_time.strftime("%H:%M"),
            ctx.author.display_name,
        )
        await self.save_reminders()

    @rappelvote_set.subcommand(
        sub_cmd_name="remove",
        sub_cmd_description="Supprime un rappel pour /journa",
    )
    async def deletereminder(self, ctx: SlashContext):
        user_id = str(ctx.user.id)
        # create the list of reminders for the user
        reminders_list = []
        for remind_time, reminder_types in reminders.copy().items():
            for reminder_type in ["NORMAL", "HARDCORE"]:
                if user_id in reminder_types[reminder_type]:
                    reminders_list.append((remind_time, reminder_type))

        # Create a button for each reminder
        buttons = [
            Button(
                label=f"{remind_time.strftime('%H:%M')} - {reminder_type.capitalize()}",
                style=ButtonStyle.SECONDARY,
                custom_id=f"{remind_time.timestamp()}_{reminder_type}",
            )
            for remind_time, reminder_type in reminders_list
        ]

        if not buttons:
            await ctx.send("Tu n'as aucun rappel configuré.", ephemeral=True)
            return

        # Send a message with the buttons, max 5 buttons per row
        components = [ActionRow(*buttons[i : i + 5]) for i in range(0, len(buttons), 5)]
        message = await ctx.send(
            "Quel rappel veux-tu supprimer ?",
            components=components,
            ephemeral=True,
        )

        try:
            # Attendre le clic sur un bouton
            button_ctx: Component = await self.bot.wait_for_component(
                components=components,
                timeout=60,
            )

            # Extraire l'timestamp et le type du custom_id
            timestamp, reminder_type = button_ctx.ctx.custom_id.split("_")
            remind_time = datetime.fromtimestamp(float(timestamp))

            # Supprimer l'utilisateur du type de rappel correspondant
            if user_id in reminders[remind_time][reminder_type]:
                reminders[remind_time][reminder_type].remove(user_id)

            # Supprimer le rappel si les deux listes sont vides
            if (
                not reminders[remind_time]["NORMAL"]
                and not reminders[remind_time]["HARDCORE"]
            ):
                del reminders[remind_time]

            # Sauvegarder et confirmer
            await self.save_reminders()
            await button_ctx.ctx.edit_origin(
                content=f"Rappel {reminder_type} à {remind_time.strftime('%H:%M')} supprimé.",
                components=[],
            )
            logger.info(
                "Rappel %s à %s supprimé pour %s",
                reminder_type,
                remind_time.strftime("%H:%M"),
                ctx.user.display_name,
            )
        except TimeoutError:
            await message.edit(content="Aucun rappel sélectionné.", components=[])

    @Task.create(IntervalTrigger(minutes=1))
    async def check_reminders(self):
        current_time = datetime.now()
        reminders_to_remove = []
        reminders_to_add = {}  # Stockage temporaire pour les nouveaux rappels
        
        async with ClientSession() as session:
            for remind_time, reminder_types in reminders.copy().items():
                if remind_time <= current_time:
                    for reminder_type in ["NORMAL", "HARDCORE"]:
                        for user_id in reminder_types[reminder_type].copy():
                            user: User = await self.bot.fetch_user(user_id)
                            try:
                                response = await session.get(
                                    f"https://zunivers-api.zerator.com/public/loot/{user.username}",
                                    headers={"X-ZUnivers-RuleSetType": reminder_type},
                                )
                                data = await response.json()
                                logger.debug(f"Récupération des données pour {user.display_name}: {data}")
                                today = current_time.strftime("%Y-%m-%d")
                                done = False

                                # Vérifier si le jour existe dans les données
                                if today in data:
                                    # Si il y a au moins une entrée pour aujourd'hui
                                    done = len(data[today]) > 0

                                if not done:
                                    message = random.choice(NORMAL_REMINDERS if reminder_type == "NORMAL" else HARDCORE_REMINDERS)
                                    await user.send(message)
                                    logger.info(
                                        f"Rappel {reminder_type} envoyé à {user.display_name}"
                                    )
                                
                                # Vérifier le calendrier festif (uniquement en décembre, jours 1-25, mode NORMAL uniquement)
                                if current_time.month == 12 and 1 <= current_time.day <= 25 and reminder_type == "NORMAL":
                                    try:
                                        calendar_response = await session.get(
                                            f"https://zunivers-api.zerator.com/public/calendar/{user.username}",
                                            headers={"X-ZUnivers-RuleSetType": "NORMAL"},
                                        )
                                        calendar_data = await calendar_response.json()
                                        
                                        # Vérifier si la case du jour est ouverte
                                        today_day = current_time.day
                                        calendar_opened = False
                                        
                                        if isinstance(calendar_data, list):
                                            for entry in calendar_data:
                                                if entry.get("day") == today_day and entry.get("openedAt") is not None:
                                                    calendar_opened = True
                                                    break
                                        
                                        if not calendar_opened:
                                            calendar_message = random.choice(ADVENT_CALENDAR_REMINDERS).format(username=user.username)
                                            await user.send(calendar_message)
                                            logger.info(
                                                f"Rappel calendrier festif envoyé à {user.display_name}"
                                            )
                                    except Exception as calendar_error:
                                        logger.warning(f"Erreur lors de la vérification du calendrier festif pour {user.display_name}: {calendar_error}")
    
                            except Exception as e:
                                if "404" not in str(e):
                                    logger.error(f"Erreur lors de l'envoi du rappel à {user.display_name}: {e}")
                                    continue
    
                            # Gestion du prochain rappel
                            next_remind = remind_time + timedelta(days=1)
                            if next_remind not in reminders_to_add:
                                reminders_to_add[next_remind] = {"NORMAL": [], "HARDCORE": []}
                            reminders_to_add[next_remind][reminder_type].append(user_id)
    
                    reminders_to_remove.append(remind_time)
    
            # Mise à jour synchronisée des rappels
            for remind_time in reminders_to_remove:
                del reminders[remind_time]
            
            # Ajout des nouveaux rappels
            for next_remind, reminder_data in reminders_to_add.items():
                if next_remind not in reminders:
                    reminders[next_remind] = {"NORMAL": [], "HARDCORE": []}
                for reminder_type in ["NORMAL", "HARDCORE"]:
                    reminders[next_remind][reminder_type].extend(reminder_data[reminder_type])
    
            await self.save_reminders()

    @Task.create(OrTrigger(*[TimeTrigger(hour=i, minute=1) for i in range(24)]))
    async def zunivers_events_checker(self):
        """
        Tâche qui vérifie les événements Zunivers toutes les heures.
        """
        await self.check_zunivers_events()

    @Task.create(TimeTrigger(23, 59, 45, utc=False))
    async def corpo_recap(self, date=None):
        bonuses_type_dict = {
            "MEMBER_COUNT": "Taille de la corporation",
            "LOOT": "Supplément par journa",
            "RECYCLE_LORE_DUST": "Supplément de poudres créatrices au recyclage",
            "RECYCLE_LORE_FRAGMENT": "Recyclage en cristaux d'histoire au recyclage",
        }
        bonus_value_dict = {
            "MEMBER_COUNT": lambda level: f"+{level * 4} membres max",
            "LOOT": lambda level: f"+{sum(range(1, level + 1)) * 10} <:eraMonnaie:1265266681291341855> par journa ou bonus",
            "RECYCLE_LORE_DUST": lambda level: f"+{sum(range(1, level + 1))}% <:eraPoudre:1265266623217012892> au recyclage",
            "RECYCLE_LORE_FRAGMENT": lambda level: f"+{sum(range(1, level + 1))}% <:eraCristal:1265266545655812118> au recyclage",
        }

        action_type_dict = {
            "LEDGER": "a donné",
            "UPGRADE": "a amélioré la corporation",
            "JOIN": "a rejoint la corporation",
            "LEAVE": "a quitté la corporation",
            "CREATE": "a créé la corporation",
        }

        # channel = await self.bot.fetch_channel(1223999470467944448)
        channel = await self.bot.fetch_channel(module_config["colocZuniversChannelId"])

        try:
            data = await fetch(
                "https://zunivers-api.zerator.com/public/corporation/ce746744-e36d-4331-a0fb-399228e66ef8",
                "json",
                headers={"X-ZUnivers-RuleSetType": "NORMAL"},
            )
        except Exception as e:
            await channel.send(f"Erreur lors de la récupération des données: {e}")
            return

        if date is None:
            date = datetime.today().date()
        else:
            try:
                date = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                await channel.send("Format de date invalide. Utilisez YYYY-MM-DD.")
                return

        # Filter and sort logs for today
        today_logs = [
            log
            for log in data["corporationLogs"]
            if datetime.strptime(log["date"], "%Y-%m-%dT%H:%M:%S.%f").date() == date
        ]
        today_logs.sort(
            key=lambda x: datetime.strptime(x["date"], "%Y-%m-%dT%H:%M:%S.%f")
        )

        # Merge logs with the same timestamp
        merged_logs = []
        i = 0
        while i < len(today_logs):
            log = today_logs[i]
            user = log["user"]
            merged_log = {
                "user": user,
                "date": log["date"],
                "action": action_type_dict[log["action"]],
                "amount": log.get("amount", 0),
            }

            # If this is an upgrade action, sum up all amounts with the same timestamp
            if log["action"] == "UPGRADE":
                j = i + 1
                while j < len(today_logs) and today_logs[j]["date"] == log["date"]:
                    merged_log["amount"] += today_logs[j].get("amount", 0)
                    j += 1
                i = j
            else:
                i += 1

            merged_logs.append(merged_log)

        # Create the corporation embed
        corporation_embed = Embed(
            title=f"{data['name']} Corporation",
            description=data["description"],
            color=0x05B600,
            url="https://zunivers.zerator.com/corporation/ce746744-e36d-4331-a0fb-399228e66ef8",
        )
        corporation_embed.set_thumbnail(url=data["logoUrl"])
        corporation_embed.add_field(
            name="Trésorerie",
            value=f"{data['balance']} <:eraMonnaie:1265266681291341855>",
            inline=True,
        )
        corporation_embed.add_field(
            name=f"Membres ({len(data['userCorporations'])})",
            value=", ".join(
                [
                    f"{member['user']['discordGlobalName']}"
                    for member in data["userCorporations"]
                ]
            ),
            inline=True,
        )

        for bonus in data["corporationBonuses"]:
            corporation_embed.add_field(
                name=f"{bonuses_type_dict[bonus['type']]} : Niv. {bonus['level']}/4",
                value=f"{bonus_value_dict[bonus['type']](bonus['level'])}",
                inline=False,
            )

        logs_embed = Embed(
            title=f"Journal de la corporation pour le {date}", color=0x05B600
        )

        str_logs = ""
        active_members = set()
        for log in merged_logs:
            if log["action"] == "a amélioré la corporation":
                action_str = f"**{log['user']['discordGlobalName']}** {log['action']} (**{log['amount']}** <:eraMonnaie:1265266681291341855>)"
            else:
                action_str = f"**{log['user']['discordGlobalName']}** {log['action']}"
                if log["amount"] != 0:
                    action_str += (
                        f" **{log['amount']}** <:eraMonnaie:1265266681291341855>"
                    )
            str_logs += f"{action_str}\n"
            active_members.add(log["user"]["discordGlobalName"])
        if not str_logs:
            str_logs = "Aucune action aujourd'hui."
        logs_embed.add_field(name="Journal", value=str_logs, inline=True)

        # Add field for inactive members
        all_members = set(
            member["user"]["discordGlobalName"] for member in data["userCorporations"]
        )
        inactive_members = all_members - active_members
        inactive_members_str = (
            ", ".join(inactive_members) if inactive_members else "Aucun"
        )
        logs_embed.add_field(name="\u200b", value="\u200b", inline=True)
        logs_embed.add_field(name="Inactifs", value=inactive_members_str, inline=True)

        # Only send the embeds if there were activities today
        if merged_logs:
            await channel.send(embeds=[corporation_embed, logs_embed])

    @slash_command(
        name="corpo",
        description="Affiche les informations de la corporation",
        scopes=[668445729928249344],
    )
    @slash_option(
        name="date",
        description="Date du récap",
        opt_type=OptionType.STRING,
        required=False,
    )
    async def corpo(self, ctx: SlashContext, date: str = None):
        await self.corpo_recap(date=date)
        await ctx.send("Corporation recap envoyé !", ephemeral=True)
