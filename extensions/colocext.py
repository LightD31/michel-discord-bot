import json
import os
import pytz
from datetime import datetime, timedelta
from aiohttp import ClientSession
from interactions import (
    ActionRow,
    BaseChannel,
    Button,
    ButtonStyle,
    Extension,
    Embed,
    IntervalTrigger,
    Message,
    OptionType,
    SlashContext,
    Task,
    TimeTrigger,
    User,
    Client,
    listen,
    slash_command,
    slash_option,
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
class ColocClass(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot

    @listen()
    async def on_startup(self):
        self.journa.start()
        await self.load_reminders()
        self.check_reminders.start()
        self.corpo_recap.start()

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
                for remind_time_str, user_ids in reminders_data.items():
                    remind_time = datetime.strptime(
                        remind_time_str, "%Y-%m-%d %H:%M:%S"
                    )
                    reminders[remind_time] = set(user_ids)
        except FileNotFoundError:
            pass

    async def save_reminders(self):
        reminders_data = {
            remind_time.strftime("%Y-%m-%d %H:%M:%S"): list(user_ids)
            for remind_time, user_ids in reminders.items()
        }
        with open(
            f"{config['misc']['dataFolder']}/journa.json", "w", encoding="utf-8"
        ) as file:
            json.dump(reminders_data, file, indent=4)

    # Set reminder to /journa
    @slash_command(
        name="journa",
        sub_cmd_name="set",
        description="Gère les rappels pour voter",
        sub_cmd_description="Ajoute un rappel pour /journa",
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
    async def rappelvote_set(self, ctx: SlashContext, heure: int, minute: int):
        remind_time = datetime.strptime(f"{heure}:{minute}", "%H:%M")
        current_time = datetime.now()
        remind_time = current_time.replace(
            hour=remind_time.hour,
            minute=remind_time.minute,
            second=0,
            microsecond=0,
        )
        if remind_time <= current_time:
            remind_time += timedelta(days=1)
        if remind_time not in reminders:
            reminders[remind_time] = set()
        reminders[remind_time].add(ctx.user.id)
        await self.save_reminders()
        await ctx.send(
            f"Rappel défini à {remind_time.strftime('%H:%M')}.", ephemeral=True
        )
        logger.info("%s a a jouté un rappel à %s", ctx.user.username, remind_time)

    @rappelvote_set.subcommand(
        sub_cmd_name="remove",
        sub_cmd_description="Supprime un rappel pour /journa",
    )
    async def deletereminder(self, ctx: SlashContext):
        user_id = ctx.user.id
        # create the list of reminders for the user
        reminders_list = []
        for remind_time, user_ids in reminders.copy().items():
            if user_id in user_ids:
                reminders_list.append(remind_time)
        # Create a button for each reminder
        buttons = [
            Button(
                label=remind_time.strftime("%H:%M"),
                style=ButtonStyle.SECONDARY,
                custom_id=str(remind_time.timestamp()),
            )
            for remind_time in reminders_list
        ]
        # Send a message with the buttons
        message = await ctx.send(
            "Quel rappel veux-tu supprimer ?",
            components=[ActionRow(*buttons)],
            ephemeral=True,
        )
        try:
            # Wait for the user to click a button
            button_ctx: Component = await self.bot.wait_for_component(
                components=[
                    str(remind_time.timestamp()) for remind_time in reminders_list
                ],
                timeout=60,
            )
            # Remove the reminder from the reminders dictionary
            remind_time = datetime.fromtimestamp(float(button_ctx.ctx.custom_id))
            reminders[remind_time].remove(user_id)
            if not reminders[remind_time]:
                del reminders[remind_time]
            # Save the reminders to a JSON file
            await self.save_reminders()
            # Send a message to the user indicating that the reminder has been removed
            await button_ctx.ctx.edit_origin(
                content=f"Rappel à {remind_time.strftime('%H:%M')} supprimé.",
                components=[],
            )
            logger.info(
                "Rappel à %s supprimé pour %s",
                remind_time.strftime("%H:%M"),
                ctx.user.display_name,
            )
        except TimeoutError:
            await ctx.send(
                "Tu n'as pas sélectionné de rappel à supprimer.", ephemeral=True
            )
            await message.edit(content="Aucun rappel sélectionné.", components=[])

    @Task.create(IntervalTrigger(minutes=1))
    async def check_reminders(self):
        current_time = datetime.now()
        reminders_to_remove = []
        async with ClientSession() as session:
            for remind_time, user_ids in reminders.copy().items():
                if remind_time <= current_time:
                    for user_id in user_ids.copy():
                        user: User = await self.bot.fetch_user(user_id)
                        # Check if the user did /journa today
                        response = await fetch(
                            f"https://zunivers-api.zerator.com/public/loot/{user.username}",
                            "json",
                            headers={"X-ZUnivers-RuleSetType": "NORMAL"}
                        )
                        for day in response["lootInfos"]:
                            if day["date"] == current_time.strftime("%Y-%m-%d"):
                                if day["count"] == 0:
                                    await user.send(
                                        "Tu n'as pas encore /journa aujourd'hui, n'oublie pas !\nhttps://discord.com/channels/138283154589876224/808432657838768168"
                                    )
                                    logger.info("Rappel envoyé à %s", user.display_name)
                                else:
                                    logger.info(
                                        "Pas de rappel pour %s, /journa déjà fait aujourd'hui.",
                                        user.display_name,
                                    )
                        next_remind_time = remind_time + timedelta(days=1)
                        if next_remind_time not in reminders:
                            reminders[next_remind_time] = set()
                        reminders[next_remind_time].add(user_id)
                        user_ids.remove(user_id)
                    if not user_ids:
                        reminders_to_remove.append(remind_time)
        for remind_time in reminders_to_remove:
            del reminders[remind_time]
        await self.save_reminders()
        
    @Task.create(TimeTrigger(23, 59, 45, utc=False))
    async def corpo_recap(self, date=None):
        bonuses_type_dict = {
            "MEMBER_COUNT": "Taille de la corporation",
            "LOOT": "Supplément par journa",
            "RECYCLE_LORE_DUST": "Supplément de poudres créatrices au recyclage",
            "RECYCLE_LORE_FRAGMENT": "Recyclage en cristaux d'histoire au recyclage"
        }
        bonus_value_dict = {
            "MEMBER_COUNT": lambda level: f"+{level * 4} membres max",
            "LOOT": lambda level: f"+{sum(range(1, level + 1)) * 10} <:eraMonnaie:1265266681291341855> par journa ou bonus",
            "RECYCLE_LORE_DUST": lambda level: f"+{sum(range(1, level + 1))}% <:eraPoudre:1265266623217012892> au recyclage",
            "RECYCLE_LORE_FRAGMENT": lambda level: f"+{sum(range(1, level + 1))}% <:eraCristal:1265266545655812118> au recyclage"
        }

        action_type_dict = {
            "LEDGER": "a donné",
            "UPGRADE": "a amélioré la corporation",
            "JOIN": "a rejoint la corporation",
            "LEAVE": "a quitté la corporation",
            "CREATE": "a créé la corporation"
        }

        # channel = await self.bot.fetch_channel(1223999470467944448)
        channel = await self.bot.fetch_channel(module_config["colocZuniversChannelId"])

        try:
            data = await fetch('https://zunivers-api.zerator.com/public/corporation/ce746744-e36d-4331-a0fb-399228e66ef8', 'json', headers={"X-ZUnivers-RuleSetType": "NORMAL"})
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
            log for log in data['corporationLogs']
            if datetime.strptime(log['date'], "%Y-%m-%dT%H:%M:%S.%f").date() == date
        ]
        today_logs.sort(key=lambda x: datetime.strptime(x['date'], "%Y-%m-%dT%H:%M:%S.%f"))

        # Merge logs with the same timestamp
        merged_logs = []
        i = 0
        while i < len(today_logs):
            log = today_logs[i]
            user = log['user']
            merged_log = {
                "user": user,
                "date": log['date'],
                "action": action_type_dict[log['action']],
                "amount": log.get('amount', 0)
            }
            
            # If this is an upgrade action, sum up all amounts with the same timestamp
            if log['action'] == "UPGRADE":
                j = i + 1
                while j < len(today_logs) and today_logs[j]['date'] == log['date']:
                    merged_log['amount'] += today_logs[j].get('amount', 0)
                    j += 1
                i = j
            else:
                i += 1
            
            merged_logs.append(merged_log)

        # Create the corporation embed
        corporation_embed = Embed(
            title=f"{data['name']} Corporation",
            description=data['description'],
            color=0x05b600,
            url="https://zunivers.zerator.com/corporation/ce746744-e36d-4331-a0fb-399228e66ef8"
        )
        corporation_embed.set_thumbnail(url=data['logoUrl'])
        corporation_embed.add_field(name="Trésorerie", value=f"{data['balance']} <:eraMonnaie:1265266681291341855>", inline=True)
        corporation_embed.add_field(name=f"Membres ({len(data['userCorporations'])})", value=", ".join([f"{member['user']['discordGlobalName']}" for member in data['userCorporations']]), inline=True)

        for bonus in data['corporationBonuses']:
            corporation_embed.add_field(
                name=f"{bonuses_type_dict[bonus['type']]} : Niv. {bonus['level']}/4",
                value=f"{bonus_value_dict[bonus['type']](bonus['level'])}",
                inline=False
            )
        
        logs_embed = Embed(
            title=f"Journal de la corporation pour le {date}",
            color=0x05b600
        )
        
        str_logs = ""
        active_members = set()
        for log in merged_logs:
            if log['action'] == "a amélioré la corporation":
                action_str = f"**{log['user']['discordGlobalName']}** {log['action']} (**{log['amount']}** <:eraMonnaie:1265266681291341855>)"
            else:
                action_str = f"**{log['user']['discordGlobalName']}** {log['action']}"
                if log['amount'] != 0:
                    action_str += f" **{log['amount']}** <:eraMonnaie:1265266681291341855>"
            str_logs += f"{action_str}\n"
            active_members.add(log['user']['discordGlobalName'])
        if not str_logs:
            str_logs = "Aucune action aujourd'hui."
        logs_embed.add_field(name="Journal", value=str_logs, inline=True)

        # Add field for inactive members
        all_members = set(member['user']['discordGlobalName'] for member in data['userCorporations'])
        inactive_members = all_members - active_members
        inactive_members_str = ", ".join(inactive_members) if inactive_members else "Aucun"
        logs_embed.add_field(name="\u200b", value="\u200b", inline=True)
        logs_embed.add_field(name="Inactifs", value=inactive_members_str, inline=True)

        # Send the embeds to the channel
        await channel.send(embeds=[corporation_embed, logs_embed])

    @slash_command(name="corpo", description="Affiche les informations de la corporation", scopes=[668445729928249344])
    @slash_option(name="date", description="Date du récap", opt_type=OptionType.STRING, required=False)
    async def corpo(self, ctx: SlashContext, date: str = None):
        await self.corpo_recap(date=date)
        await ctx.send("Corporation recap envoyé !", ephemeral=True)