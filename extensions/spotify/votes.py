"""Daily poll, add-with-vote, rappelvote reminders, and button handlers."""

import os
import random
from datetime import datetime, timedelta

import pymongo
import spotipy
from interactions import (
    ActionRow,
    AutocompleteContext,
    Button,
    ButtonStyle,
    IntervalTrigger,
    MaterialColors,
    OptionType,
    OrTrigger,
    SlashContext,
    Task,
    Timestamp,
    TimestampStyles,
    TimeTrigger,
    listen,
    slash_command,
    slash_option,
)
from interactions.api.events import Component
from interactions.client.utils import timestamp_converter

from src.core import logging as logutil
from src.discord_ext.messages import fetch_user_safe, send_error
from src.integrations.spotify import spotifymongoformat

from ._common import (
    COOLDOWN_TIME,
    DEV_GUILD,
    SERVERS,
    EmbedType,
    ServerData,
    count_votes,
    embed_message_vote,
    embed_message_vote_add,
    embed_song,
    enabled_servers,
    sp,
)
from ._cooldown import VoteCooldown

logger = logutil.init_logger(os.path.basename(__file__))

# Mongo-backed per-user cooldown shared across both button handlers
# (conserver/supprimer/menfou + addwithvote). TTL indexes auto-expire entries.
vote_cooldown = VoteCooldown(COOLDOWN_TIME)


class VotesMixin:
    """Slash commands, tasks, and component handlers for playlist voting."""

    @Task.create(TimeTrigger(hour=20, minute=0, utc=False))
    async def randomvote(self):
        """Daily keep/remove poll — closes the previous one and opens the next."""
        for server in SERVERS.values():
            try:
                await self._randomvote_for_server(server)
            except Exception as e:
                logger.error("Error in randomvote for server %s: %s", server.guild_id, e)

    async def _randomvote_for_server(self, server: ServerData):
        logger.info("Tache randomvote lancée pour le serveur %s", server.guild_id)
        message_id = server.vote_infos.get("message_id")
        track_id = server.vote_infos.get("track_id")
        if not message_id or not track_id:
            logger.warning(
                "Pas de vote en cours pour le serveur %s, lancement d'un nouveau vote",
                server.guild_id,
            )
            await self._start_new_vote(server)
            return
        logger.debug("message_id: %s", message_id)
        logger.debug("track_id: %s", track_id)
        channel = self.bot.get_channel(server.channel_id)
        message = await channel.fetch_message(message_id)
        logger.debug("message : %s", str(message.id))
        votes = await server.votes_db.find_one({"_id": track_id})
        song = await server.playlist_items_full.find_one({"_id": track_id})
        logger.debug("song : %s\ntrack_id : %s", song, track_id)
        track = sp.track(track_id, market="FR")
        conserver, supprimer, menfou, users = count_votes(votes["votes"], server.discord2name)

        total_votes = conserver + supprimer + menfou
        if total_votes < 3:
            new_time = str(self.randomvote.next_run)
            embed_original = message.embeds[0]
            embed_original.title = (
                f"Vote prolongé jusqu'à "
                f"{timestamp_converter(new_time).format(TimestampStyles.RelativeTime)}"
            )
            embed_original.timestamp = new_time
            await message.edit(
                content=(
                    f"Pas assez de votes ({total_votes}/3), le vote est prolongé de 24h !\n"
                    f"Voulez-vous **conserver** cette chanson dans playlist ? "
                    f"(poke <@{song['added_by']}>)"
                ),
                embeds=[embed_original],
            )
            logger.info(f"Vote prolongé de 24h car seulement {total_votes} votes")
            return

        logger.debug(
            "keep : %s\nremove : %s\nmenfou : %s",
            str(conserver),
            str(supprimer),
            str(menfou),
        )

        await message.unpin()
        if supprimer > conserver or (conserver == 0 and supprimer == 0 and menfou >= 3):
            embed, file = await embed_song(
                song=song,
                track=track,
                embedtype=EmbedType.VOTE_LOSE,
                time=Timestamp.now(),
            )
            await message.edit(
                content="La chanson a été supprimée.",
                embeds=[
                    embed,
                    await embed_message_vote(
                        keep=conserver,
                        remove=supprimer,
                        menfou=menfou,
                        users=users,
                        color=MaterialColors.DEEP_ORANGE,
                    ),
                ],
                components=[],
            )
            sp.playlist_remove_all_occurrences_of_items(server.playlist_id, [track_id])
            await server.playlist_items_full.delete_one({"_id": track_id})
            await server.votes_db.find_one_and_update(
                {"_id": track_id}, {"$set": {"state": "supprimée"}}
            )
            logger.info("La chanson a été supprimée.")
            await self._check_playlist_changes_for_server(server)
        else:
            logger.debug("La chanson a été conservée.")
            logger.debug("track_id : %s\nmessage_id : %s", track_id, message_id)
            embed, file = await embed_song(
                song=song,
                track=track,
                embedtype=EmbedType.VOTE_WIN,
                time=Timestamp.now(),
            )
            await message.edit(
                content="La chanson a été conservée.",
                embeds=[
                    embed,
                    await embed_message_vote(
                        keep=conserver,
                        remove=supprimer,
                        menfou=menfou,
                        users=users,
                        color=MaterialColors.LIME,
                    ),
                ],
                components=[],
            )
            await server.votes_db.find_one_and_update(
                {"_id": track_id}, {"$set": {"state": "conservée"}}
            )
            logger.info("La chanson a été conservée.")
        await self._start_new_vote(server)

    async def _start_new_vote(self, server: ServerData):
        """Pick a fresh track (not previously voted on) and open a new poll."""
        track_ids = set(await server.playlist_items_full.distinct("_id"))
        pollhistory = set(await server.votes_db.distinct("_id"))
        track_id = random.choice(list(track_ids))
        logger.debug("track_id choisie : %s", track_id)
        while track_id in pollhistory:
            logger.warning("Chanson déjà votée, nouvelle chanson tirée au sort (%s)", track_id)
            track_id = random.choice(list(track_ids))
        logger.info("Chanson tirée au sort : %s", track_id)
        song = await server.playlist_items_full.find_one({"_id": track_id})
        track = sp.track(song["_id"], market="FR")
        channel = await self.bot.fetch_channel(server.channel_id)
        embed, file = await embed_song(
            song=song,
            track=track,
            embedtype=EmbedType.VOTE,
            time=str(self.randomvote.next_run),
        )
        message = await channel.send(
            content=(
                f"Voulez-vous **conserver** cette chanson dans playlist ? "
                f"(poke <@{song['added_by']}>)"
            ),
            embeds=[embed],
            components=[
                ActionRow(
                    Button(
                        label="Conserver",
                        style=ButtonStyle.SUCCESS,
                        emoji="✅",
                        custom_id="conserver",
                    ),
                    Button(
                        label="Supprimer",
                        style=ButtonStyle.DANGER,
                        emoji="🗑️",
                        custom_id="supprimer",
                    ),
                    Button(
                        label="Menfou",
                        style=ButtonStyle.SECONDARY,
                        emoji="🤷",
                        custom_id="menfou",
                    ),
                    Button(
                        label="Annuler",
                        style=ButtonStyle.SECONDARY,
                        emoji="❌",
                        custom_id="annuler",
                    ),
                ),
            ],
            files=[file] if file else None,
        )
        await message.pin()
        await channel.purge(deletion_limit=1, after=message)
        server.vote_infos.update({"message_id": str(message.id), "track_id": track_id})
        await self.save_voteinfos(server)
        await server.votes_db.update_one(
            {"_id": track_id},
            {
                "$set": {
                    "name": (
                        f"{', '.join(artist['name'] for artist in track['artists'])} "
                        f"- {track['name']}"
                    ),
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "added_by": song["added_by"],
                    "votes": {},
                }
            },
            upsert=True,
        )

    @listen(Component)
    async def on_component(self, event: Component):
        """Handle keep/remove/menfou/annuler button clicks on the daily poll message."""
        ctx = event.ctx
        logger.debug("ctx.custom_id : %s", ctx.custom_id)
        if ctx.custom_id not in ["conserver", "supprimer", "menfou", "annuler"]:
            return
        server = self.get_server(ctx.guild_id)
        user_id = str(ctx.user.id)
        if await vote_cooldown.is_on_cooldown(user_id):
            await send_error(ctx, "Tu ne peux voter que toutes les 5 secondes.")
            logger.warning("%s a essayé de voter trop rapidement", ctx.user.username)
            return
        await vote_cooldown.record(user_id)
        message_id = server.vote_infos.get("message_id")
        track_id = server.vote_infos.get("track_id")
        if ctx.message.id == int(message_id):
            embed_original = ctx.message.embeds[0]
            user_id = str(ctx.user.id)
            if ctx.custom_id == "annuler":
                votes = await server.votes_db.find_one_and_update(
                    {"_id": track_id},
                    {"$unset": {f"votes.{user_id}": ""}},
                    return_document=pymongo.ReturnDocument.AFTER,
                )
            else:
                votes = await server.votes_db.find_one_and_update(
                    {"_id": track_id},
                    {"$set": {f"votes.{user_id}": ctx.custom_id}},
                    upsert=True,
                    return_document=pymongo.ReturnDocument.AFTER,
                )
            logger.info("User %s voted %s", ctx.user.username, ctx.custom_id)
            conserver, supprimer, menfou, users = count_votes(votes["votes"], server.discord2name)
            users = ", ".join(users)
            logger.info(
                "Votes : %s conserver, %s supprimer, %s menfou",
                conserver,
                supprimer,
                menfou,
            )
            total = conserver + supprimer + menfou
            embed_original.fields[4].value = f"{total} vote{'s' if total > 1 else ''} ({users})"

            await ctx.message.edit(embeds=[embed_original])

            if ctx.custom_id == "annuler":
                await ctx.send("Ton vote a bien été annulé ! 🗳️", ephemeral=True)
            else:
                await ctx.send(
                    f"Ton vote pour **{ctx.custom_id}** cette musique a bien été pris en compte ! 🗳️",
                    ephemeral=True,
                )

    @slash_command(
        name="rappelvote",
        sub_cmd_name="set",
        description="Gère les rappels pour voter",
        sub_cmd_description="Ajoute un rappel pour voter pour la chanson du jour",
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
    async def setreminder(self, ctx: SlashContext, heure, minute):
        """Schedule a recurring daily DM nudging the user to vote."""
        server = self.get_server(ctx.guild_id)
        if str(ctx.channel_id) == str(server.channel_id):
            logger.info("%s a ajouté un rappel à %s:%s", ctx.user.display_name, heure, minute)
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
            if remind_time not in server.reminders:
                server.reminders[remind_time] = set()
            server.reminders[remind_time].add(ctx.user.id)
            await self.save_reminders(server)

            await ctx.send(f"Rappel défini à {remind_time.strftime('%H:%M')}.", ephemeral=True)
        else:
            await send_error(ctx, "Cette commande n'est pas disponible dans ce salon.")
            logger.info(
                "%s a essayé d'utiliser la commande /rappel dans le salon #%s (%s)",
                ctx.user.display_name,
                ctx.channel.name,
                ctx.channel_id,
            )

    @Task.create(IntervalTrigger(minutes=1))
    async def reminder_check(self):
        for server in SERVERS.values():
            try:
                await self._reminder_check_for_server(server)
            except Exception as e:
                logger.error("Error in reminder_check for server %s: %s", server.guild_id, e)

    async def _reminder_check_for_server(self, server: ServerData):
        logger.debug("reminder_check lancé pour le serveur %s", server.guild_id)
        current_time = datetime.now()
        reminders_to_remove = []
        for remind_time, user_ids in server.reminders.copy().items():
            if current_time >= remind_time:
                for user_id in user_ids.copy():
                    _, user = await fetch_user_safe(self.bot, user_id)
                    if user:
                        vote_doc = await server.votes_db.find_one(
                            {"_id": str(server.vote_infos["track_id"])}
                        )
                        vote = vote_doc["votes"].get(str(user_id)) if vote_doc else None
                        if vote is None:
                            await user.send(
                                f"Hey {user.mention}, tu n'as pas voté aujourd'hui "
                                f":pleading_face: \n"
                                f"https://discord.com/channels/{server.guild_id}/"
                                f"{server.channel_id}/{server.vote_infos.get('message_id')}"
                            )
                            logger.debug("Rappel envoyé à %s", user.display_name)
                        else:
                            logger.debug(
                                "%s a déjà voté aujourd'hui !, pas de rappel envoyé",
                                user.display_name,
                            )
                    next_remind_time = remind_time + timedelta(days=1)
                    if next_remind_time not in server.reminders:
                        server.reminders[next_remind_time] = set()
                    server.reminders[next_remind_time].add(user_id)
                    user_ids.remove(user_id)
                if not user_ids:
                    reminders_to_remove.append(remind_time)
        for remind_time in reminders_to_remove:
            del server.reminders[remind_time]

        await self.save_reminders(server)

    @setreminder.subcommand(
        sub_cmd_name="remove",
        sub_cmd_description="Enlève un rappel de vote pour la chanson du jour",
    )
    async def deletereminder(self, ctx: SlashContext):
        server = self.get_server(ctx.guild_id)
        user_id = ctx.user.id
        reminders_list = []
        for remind_time, user_ids in server.reminders.copy().items():
            if user_id in user_ids:
                reminders_list.append(remind_time)
        buttons = [
            Button(
                label=remind_time.strftime("%H:%M"),
                style=ButtonStyle.SECONDARY,
                custom_id=str(remind_time.timestamp()),
            )
            for remind_time in reminders_list
        ]
        await ctx.send(
            "Quel rappel veux-tu supprimer ?",
            components=[ActionRow(*buttons)],
            ephemeral=True,
        )
        try:
            button_ctx: Component = await self.bot.wait_for_component(
                components=[str(remind_time.timestamp()) for remind_time in reminders_list],
                timeout=60,
            )
            remind_time = datetime.fromtimestamp(float(button_ctx.ctx.custom_id))
            server.reminders[remind_time].remove(user_id)
            if not server.reminders[remind_time]:
                del server.reminders[remind_time]
            await self.save_reminders(server)
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
            await send_error(ctx, "Tu n'as pas sélectionné de rappel à supprimer.")
            await button_ctx.ctx.edit_origin(content="Aucun rappel sélectionné.", components=[])

    @slash_command(
        name="addwithvote",
        description="Si vous êtes pas sûr d'ajoouter une chanson, vous pouvez la mettre au vote",
        scopes=enabled_servers,
    )
    @slash_option(
        name="song",
        description="Nom de la chanson",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def addwithvote(self, ctx: SlashContext, song):
        server = self.get_server(ctx.guild_id)
        if str(ctx.channel_id) == str(server.channel_id):
            last_track_ids = await server.playlist_items_full.distinct("_id")
            logger.info(
                "/addwithvote '%s' utilisé par %s(id:%s)",
                song,
                ctx.author.username,
                ctx.author_id,
            )
            try:
                track = sp.track(song, market="FR")
                song = spotifymongoformat(
                    track, ctx.author_id, spotify2discord=server.spotify2discord
                )
            except spotipy.exceptions.SpotifyException:
                await send_error(ctx, "Cette chanson n'existe pas.")
                logger.info("Commande /addsong utilisée avec une chanson inexistante")
            data = await server.vote_manager.load_data()
            song_ids = list(data.keys())
            if song["_id"] not in last_track_ids and song["_id"] not in song_ids:
                logger.debug("song : %s", song)
                components = [
                    ActionRow(
                        Button(
                            label="Oui",
                            style=ButtonStyle.SUCCESS,
                            emoji="✅",
                            custom_id=f"addwithvote_{song['_id']}_yes",
                        ),
                        Button(
                            label="Non",
                            style=ButtonStyle.DANGER,
                            emoji="🗑️",
                            custom_id=f"addwithvote_{song['_id']}_no",
                        ),
                        Button(
                            label="Annuler",
                            style=ButtonStyle.SECONDARY,
                            emoji="❌",
                            custom_id=f"addwithvote_{song['_id']}_annuler",
                        ),
                    ),
                ]
                time_deadline = (datetime.now() + timedelta(days=1)).replace(
                    minute=0, second=0, microsecond=0
                )
                if time_deadline < datetime.now() + timedelta(days=1):
                    time_deadline += timedelta(hours=1)
                embed, file = await embed_song(
                    song=song,
                    track=track,
                    embedtype=EmbedType.VOTE_ADD,
                    time=time_deadline,
                    person=ctx.author.id,
                    icon=ctx.author.avatar.url,
                )
                message = await ctx.send(
                    content=(
                        f"Voulez-vous **ajouter** cette chanson à la playlist ? "
                        f"(Demandé par <@{ctx.author_id}>)\n"
                        f"{track['external_urls']['spotify']}"
                    ),
                    embeds=embed,
                    files=[file] if file else None,
                    components=components,
                )
                data = await server.vote_manager.load_data()
                data[song["_id"]] = {
                    "channel_id": ctx.channel.id,
                    "message_id": message.id,
                    "author_id": ctx.author.id,
                    "deadline": time_deadline.timestamp(),
                    "votes": {str(ctx.author.id): "yes"},
                }
                await server.vote_manager.save_data(data)
                logger.info("%s ajouté au vote par %s", track["name"], ctx.author.display_name)
            else:
                await send_error(ctx, "Cette chanson est déjà dans la playlist.")
                logger.info("Commande /addwithvote utilisée avec une chanson déjà présente")
        else:
            await send_error(ctx, "Vous ne pouvez pas utiliser cette commande dans ce salon.")
            logger.info(
                "Commande /addwithvote utilisée dans un mauvais salon(%s)",
                ctx.channel.name,
            )

    @listen(Component)
    async def on_button2(self, event: Component):
        if not event.ctx.custom_id.startswith("addwithvote"):
            return
        server = self.get_server(event.ctx.guild_id)
        song_id = event.ctx.custom_id.split("_")[1]
        vote = event.ctx.custom_id.split("_")[2]
        user_id = str(event.ctx.user.id)
        if await vote_cooldown.is_on_cooldown(user_id):
            await send_error(event.ctx, "Tu ne peux voter que toutes les 5 secondes.")
            logger.warning("%s a essayé de voter trop rapidement", event.ctx.user.username)
            return
        await vote_cooldown.record(user_id)
        data = await server.vote_manager.load_data()
        if vote == "annuler":
            data[song_id]["votes"].pop(user_id, None)
            await server.vote_manager.save_data(data)
        else:
            await server.vote_manager.save_vote(user_id, vote, song_id)
        data = await server.vote_manager.load_data()
        yes, no, users = server.vote_manager.count_votes(data, song_id)
        users = ", ".join(users)
        embed_original = event.ctx.message.embeds[0]
        embed_original.fields[4].value = f"{yes + no} vote{'s' if yes + no > 1 else ''} ({users})"
        await event.ctx.message.edit(embeds=[embed_original])
        if vote == "annuler":
            await event.ctx.send("Ton vote a bien été annulé ! 🗳️", ephemeral=True)
        else:
            await event.ctx.send(
                f"Ton vote pour **{vote}** cette musique a bien été pris en compte ! 🗳️",
                ephemeral=True,
            )
        logger.info("User %s voted %s", event.ctx.user.username, vote)

    @addwithvote.autocomplete("song")
    async def autocomplete_from_spotify_addwithvote(self, ctx: AutocompleteContext):
        if not ctx.input_text:
            choices = [{"name": "Veuillez entrer un nom de chanson", "value": "error"}]
        else:
            items = sp.search(ctx.input_text, limit=10, type="track", market="FR")["tracks"][
                "items"
            ]
            if not items:
                choices = [{"name": "Aucun résultat", "value": "error"}]
            else:
                choices = [
                    {
                        "name": (
                            f"{item['artists'][0]['name']} - {item['name']} "
                            f"(Album: {item['album']['name']})"
                        )[:100],
                        "value": item["uri"],
                    }
                    for item in items
                ]
        await ctx.send(choices=choices)

    async def endvote(self, song_id: str, server: ServerData):
        """Close an add-with-vote poll once its deadline is reached."""
        data = await server.vote_manager.load_data()
        yes_votes, no_votes, users = server.vote_manager.count_votes(data, song_id)
        channel = await self.bot.fetch_channel(data[song_id]["channel_id"])
        message = await channel.fetch_message(data[song_id]["message_id"])
        try:
            track = sp.track(song_id, market="FR")
            song = spotifymongoformat(
                track, data[song_id]["author_id"], spotify2discord=server.spotify2discord
            )
        except spotipy.exceptions.SpotifyException as e:
            logger.error("Spotify API Error while using /addwithvote: %s", e)
            return
        if yes_votes > no_votes:
            logger.debug("song : %s", song)
            await server.playlist_items_full.insert_one(song)
            sp.playlist_add_items(server.playlist_id, [song["_id"]])
            embed, file = await embed_song(
                song=song,
                track=track,
                embedtype=EmbedType.VOTE_WIN,
                time=Timestamp.utcnow(),
                person=data[song_id]["author_id"],
            )
            await message.edit(
                content="La chanson a été ajoutée à la playlist.",
                embeds=[embed, await embed_message_vote_add(yes_votes, no_votes, users)],
                components=[],
            )
            logger.info("La chanson a été ajoutée à la playlist.")
        else:
            embed, file = await embed_song(
                song=song,
                track=track,
                embedtype=EmbedType.VOTE_LOSE,
                time=Timestamp.utcnow(),
                person=data[song_id]["author_id"],
            )
            await message.edit(
                content="La chanson n'a pas été ajoutée à la playlist.",
                embeds=[embed, await embed_message_vote_add(yes_votes, no_votes, users)],
                components=[],
            )
            logger.info("La chanson n'a pas été ajoutée à la playlist.")
        data.pop(song_id)
        await server.vote_manager.save_data(data)

    @Task.create(OrTrigger(*[TimeTrigger(hour=hour) for hour in range(24)]))
    async def check_for_end(self):
        """Hourly sweep: close any add-with-vote poll whose deadline has passed."""
        for server in SERVERS.values():
            try:
                data = await server.vote_manager.load_data()
                songs_to_end = []
                for song_id in data:
                    if await server.vote_manager.check_deadline(song_id):
                        await self.endvote(song_id, server)
                        songs_to_end.append(song_id)
                for song_id in songs_to_end:
                    data.pop(song_id)
                await server.vote_manager.save_data(data)
            except Exception as e:
                logger.error("Error in check_for_end for server %s: %s", server.guild_id, e)

    @slash_command(
        name="nextvote",
        description="Force le prochain vote PAS TOUCHE",
        scopes=[DEV_GUILD],
    )
    async def nextvote(self, ctx: SlashContext):
        """Dev-only: force an immediate close+restart of the daily vote cycle."""
        await self.randomvote()
        await ctx.send("Vote forcé", ephemeral=True)
