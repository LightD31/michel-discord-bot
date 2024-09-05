from typing import List, Dict, Any, Tuple
from interactions import (
    Task,
    IntervalTrigger,
    Extension,
    listen,
    Embed,
    Client,
    TimestampStyles,
)
from interactions.client.utils import timestamp_converter
from src import logutil
from src.raiderio import get_table_data, ensure_six_elements
from src.utils import load_config, fetch
from datetime import datetime, timedelta

logger = logutil.init_logger(__name__)
config, module_config, enabled_servers = load_config("moduleLiquipedia")
# Server specific module
module_config = module_config[enabled_servers[0]]
api_key = config["liquipedia"]["liquipediaApiKey"]


class Liquipedia(Extension):
    def __init__(self, bot):
        self.bot: Client = bot
        self.message = None
        self.wow_message = None

    @listen()
    async def on_startup(self):
        channel_id = module_config["liquipediaChannelId"]
        message_id = module_config["liquipediaMessageId"]
        wow_channel_id = module_config["liquipediaWowChannelId"]
        wow_message_id = module_config["liquipediaWowMessageId"]
        channel = await self.bot.fetch_channel(channel_id)
        self.message = await channel.fetch_message(message_id)
        channel = await self.bot.fetch_channel(wow_channel_id)
        self.wow_message = await channel.fetch_message(wow_message_id)
        self.schedule.start()
        # self.mdi_schedule.start()
        # await self.mdi_schedule()
        # await self.schedule()

    @Task.create(IntervalTrigger(minutes=5))
    async def schedule(self):
        logger.debug("Running Liquipedia schedule task")
        try:
            team = "Mandatory"
            date = (datetime.now() - timedelta(weeks=7)).strftime("%Y-%m-%d")
            data = await self.liquipedia_request(
                "valorant",
                "match",
                f"[[opponent::{team}]] AND [[date::>{date}]]",
                limit=15,
                order="date ASC",
            )
            embeds, pagenames = await self.make_schedule_embed(data, team)

            for pagename in pagenames:
                tournament = await self.liquipedia_request(
                    "valorant",
                    "tournament",
                    f"[[pagename::{pagename}]]",
                    query="participantsnumber, name",
                )
                participants_number = int(tournament["result"][0]["participantsnumber"])
                tournament_name = tournament["result"][0]["name"]
                standings = await self.liquipedia_request(
                    "valorant",
                    "standingsentry",
                    f"[[parent::{pagename}]]",
                    limit=participants_number * 2,
                    order="roundindex DESC",
                )
                clean_standings = await self.organize_standings(standings)
                for pageid in clean_standings:
                    embeds.append(
                        await self.make_standings_embed(
                            clean_standings[pageid], f"Classement de {tournament_name}"
                        )
                    )
            await self.message.edit(embeds=embeds)
        except Exception as e:
            logger.error(f"Error in schedule task: {e}")

    async def liquipedia_request(
        self,
        wiki: str,
        datapoint: str,
        conditions: str = "",
        query: str = "",
        limit: str = "",
        offset: str = "",
        order: str = "",
    ) -> Dict[str, Any]:
        headers = {"Authorization": f"Apikey {api_key}"}
        params = {
            "wiki": wiki,
            "conditions": conditions,
            "query": query,
            "limit": limit,
            "offset": offset,
            "order": order,
        }
        url = f"https://api.liquipedia.net/api/v3/{datapoint}"
        logger.debug(f"Request to Liquipedia: {url} with params: {params}")
        return await fetch(url, headers=headers, params=params, return_type="json")

    async def organize_standings(
        self, data: Dict[str, Any]
    ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        organized = {}
        for entry in data["result"]:
            pageid = entry["pageid"]
            roundindex = entry["roundindex"]
            organized.setdefault(pageid, {}).setdefault(roundindex, []).append(
                {
                    "team": entry["opponentname"],
                    "standing": entry["placement"],
                    "match": {
                        "win": entry["scoreboard"]["match"]["w"],
                        "loss": entry["scoreboard"]["match"]["l"],
                        "draw": entry["scoreboard"]["match"]["d"],
                    },
                    "game": {
                        "win": entry["scoreboard"]["game"]["w"],
                        "loss": entry["scoreboard"]["game"]["l"],
                        "draw": entry["scoreboard"]["game"]["d"],
                    },
                    "diff_rounds": (
                        f"+{entry['scoreboard']['diff']}"
                        if entry["scoreboard"]["diff"] > 0
                        else entry["scoreboard"]["diff"]
                    ),
                    "placementchange": entry["placementchange"],
                    "currentstatus": entry["currentstatus"],
                    "definitestatus": entry["definitestatus"],
                }
            )

        for pageid in organized:
            for roundindex in organized[pageid]:
                organized[pageid][roundindex].sort(key=lambda entry: entry["standing"])
        return organized

    async def make_standings_embed(
        self, data: Dict[str, List[Dict[str, Any]]], name: str = "Classement"
    ) -> Embed:
        embed = Embed(
            name, color=0xE04747, footer="Source: Liquipedia", timestamp=datetime.now()
        )

        def format_change(placement_change):
            if placement_change > 0:
                return f"\u001b[1;32m▲{placement_change}\u001b[0m"
            elif placement_change < 0:
                return f"\u001b[1;31m▼{-placement_change}\u001b[0m"
            return "\u001b[1;30m==\u001b[0m"

        def format_status(status, text, bold=False):
            bold = int(bold)
            colors = {
                "up": f"\u001b[{bold};32m",
                "down": f"\u001b[{bold};31m",
                "stay": f"\u001b[{bold};33m",
            }
            return f"{colors.get(status, '')}{text}\u001b[0m"

        for week, standings in data.items():
            string = "```ansi\n"
            for team in standings:
                diff_txt = format_change(team["placementchange"])
                standing_str = format_status(
                    team["currentstatus"], team["standing"], True
                )
                team_str = format_status(team["definitestatus"], f"{team['team']:<14}")
                string += f"{standing_str} {team_str} ({team['match']['win']}-{team['match']['loss']}) {diff_txt} ({team['diff_rounds']})\n"
            string += "```"
            embed.add_field(name=f"Semaine {week}", value=string)
        return embed

    def format_past_match(
        self,
        match: Dict[str, Any],
        score_1: int,
        score_2: int,
        name: str,
    ) -> Dict[str, str]:
        name_1 = match["match2opponents"][0]["name"]
        name_2 = match["match2opponents"][1]["name"]
        shortname_1 = match["match2opponents"][0]["teamtemplate"]["shortname"]
        shortname_2 = match["match2opponents"][1]["teamtemplate"]["shortname"]

        winner = int(match["winner"]) - 1
        winner_name = match["match2opponents"][winner]["name"]
        date = timestamp_converter(match["extradata"]["timestamp"])
        resultat = (
            "Gagné <:zrtHypers:1257757857122877612>"
            if winner_name == name
            else f"Perdu <:zrtCry:1257757854861885571>"
        )
        games = ""
        map_veto = match["extradata"]["mapveto"]
        for game in match["match2games"]:
            if game["resulttype"] == "np":
                break
            # Get who picked or banned the map
            map_name = game["map"]
            veto_info = ""
            for veto in map_veto.values():
                if veto.get("team1") == map_name:
                    veto_info = f"(Pick {shortname_1})"
                    break
                elif veto.get("team2") == map_name:
                    veto_info = f"(Pick {shortname_2})"
                    break
                elif veto["type"] == "decider" and veto.get("decider") == map_name:
                    veto_info = ""
                    break

            # Format the scores
            map_score_1 = int(game["scores"][0])
            map_score_2 = int(game["scores"][1])
            if map_score_1 > map_score_2:
                game_result = f"**{map_score_1}**-{map_score_2}"
            elif map_score_2 > map_score_1:
                game_result = f"{map_score_1}-**{map_score_2}**"
            else:
                game_result = f"{map_score_1}-{map_score_2}"
            games += f"**{map_name}** : {game_result} {veto_info}\n"

        return {
            "name": f"{name_1} {score_1}-{score_2} {name_2} (Bo{match['bestof']})",
            "value": f"{match['tickername']}\n{date}\n{games}{resultat}",
        }

    def format_ongoing_match(
        self,
        match: Dict[str, Any],
        score_1: int,
        score_2: int,
    ) -> Dict[str, str]:
        name_1 = match["match2opponents"][0]["name"]
        name_2 = match["match2opponents"][1]["name"]
        shortname_1 = match["match2opponents"][0]["teamtemplate"]["shortname"]
        shortname_2 = match["match2opponents"][1]["teamtemplate"]["shortname"]
        embeds = []
        embeds.append(
            {
                "name": f"<:zrtON:962320783038890054> {name_1} {score_1}-{score_2} {name_2} en Bo{match['bestof']} <:zrtON:962320783038890054>",
                "value": f"En cours\n{match['tickername']}",
            }
        )
        map_veto = match["extradata"].get("mapveto", {})
        for game in match["match2games"]:
            map_name = game["map"]
            # Fetching players and their agents
            players_team1 = []
            players_team2 = []

            if isinstance(game["participants"], dict):
                for participant in game["participants"]:
                    if isinstance(participant, dict):
                        player_name = participant.get("player")
                        agent_name = participant.get("agent")
                        team = participant.get("team")
                        if player_name and agent_name:
                            player_info = f"{player_name}: {agent_name}"
                            if team == name_1:
                                players_team1.append(player_info)
                            elif team == name_2:
                                players_team2.append(player_info)

            # Format players info in two columns
            max_players = max(len(players_team1), len(players_team2))
            players_info = "\n".join(
                f"{players_team1[i] if i < len(players_team1) else '':<30} {players_team2[i] if i < len(players_team2) else ''}"
                for i in range(max_players)
            )
            # Determine veto info
            if map_veto != {}:
                for veto in map_veto.values():
                    if veto.get("team1") == map_name:
                        veto_info = f"(Pick {shortname_1})"
                        break
                    elif veto.get("team2") == map_name:
                        veto_info = f"(Pick {shortname_2})"
                        break
                    elif veto["type"] == "decider" and veto.get("decider") == map_name:
                        veto_info = "(Decider)"
                        break
            else:
                veto_info = ""
            # Format the scores, show empty if not available
            if game["resulttype"] != "np" and game["scores"] != []:
                map_score_1 = int(game["scores"][0])
                map_score_2 = int(game["scores"][1])
                if map_score_1 > map_score_2:
                    game_result = f"**{map_score_1}**-{map_score_2}"
                elif map_score_2 > map_score_1:
                    game_result = f"{map_score_1}-**{map_score_2}**"
                else:
                    game_result = f"{map_score_1}-{map_score_2}"

                embed = {
                    "name": f"**{map_name}** {veto_info}",
                    "value": f"{shortname_1} {game_result} {shortname_2}\n{players_info}",
                }
            else:
                embed = {"name": f"**{map_name}** {veto_info}", "value": "\u200b"}

            embeds.append(embed)
        return embeds

    def format_upcoming_match(
        self,
        match: Dict[str, Any],
    ) -> Dict[str, str]:
        return {
            "name": f"{match['match2opponents'][0]['name']} vs {match['match2opponents'][1]['name']} (Bo{match['bestof']})",
            "value": f"{timestamp_converter(match['extradata']['timestamp'])}\n{match['tickername']}",
        }

    async def make_schedule_embed(
        self, data: Dict[str, Any], name: str
    ) -> Tuple[List[Embed], List[str]]:
        past_embed = Embed(
            title=f"Derniers matchs de {name}", color=0xE04747, timestamp=datetime.now()
        )
        ongoing_embed = Embed(
            title=f"Match en cours de {name}", color=0xE04747, timestamp=datetime.now()
        )
        upcoming_embed = Embed(
            title=f"Prochains matchs de {name}",
            color=0xE04747,
            timestamp=datetime.now(),
        )
        for embed in (past_embed, ongoing_embed, upcoming_embed):
            embed.set_footer(text="Source: Liquipedia")
        parents = []
        current_time = datetime.now().timestamp()
        past_count, upcoming_count = 0, 0

        def add_dummy_field(embed, count):
            if count % 2 != 0:
                embed.add_field(name="\u200b", value="\u200b", inline=True)

        for match in data["result"]:
            if match["parent"] not in parents:
                parents.append(match["parent"])
            match_timestamp = match["extradata"]["timestamp"]
            score_1 = sum(
                1 for game in match["match2games"] if game.get("winner") == "1"
            )
            score_2 = sum(
                1 for game in match["match2games"] if game.get("winner") == "2"
            )
            if match_timestamp < current_time:
                if match["finished"] == 0:
                    fields = self.format_ongoing_match(match, score_1, score_2)
                    for field in fields:
                        ongoing_embed.add_field(
                            name=field["name"], value=field["value"], inline=False
                        )
                elif match["finished"] == 1 and past_count < 6:
                    field = self.format_past_match(match, score_1, score_2, name)
                    past_embed.add_field(
                        name=field["name"], value=field["value"], inline=True
                    )
                    past_count += 1
                    add_dummy_field(past_embed, past_count)
            elif upcoming_count < 6:
                field = self.format_upcoming_match(match)
                upcoming_embed.add_field(
                    name=field["name"], value=field["value"], inline=True
                )
                upcoming_count += 1
                add_dummy_field(upcoming_embed, upcoming_count)

        embeds_to_return = [
            embed
            for embed in (past_embed, ongoing_embed, upcoming_embed)
            if embed.fields
        ]
        logger.debug(f"Embeds created: {[embed.title for embed in embeds_to_return]}")
        logger.debug(f"Parents: {parents}")
        return embeds_to_return, parents
    
    @Task.create(IntervalTrigger(minutes=5))
    async def mdi_schedule(self):
        data, dungeons = await get_table_data()
        infos = await self.mdi_infos()
        safe_teams = data
        in_danger_teams = []
        out_teams = []
        dungeons = ensure_six_elements(dungeons, "???")
        # Prepare the infos_str section (assuming it remains unchanged)
        infos_str = f"""Du {timestamp_converter(infos['start_date']).format(TimestampStyles.LongDate)} au {timestamp_converter(infos['end_date']).format(TimestampStyles.LongDate)}\nCashprize: **${infos['prizepool']} USD**
    
    **Day 1: July 05th**
    6 teams compete over 5 hours in 3 dungeons ({', '.join(dungeons[:3])})\n
    **Day 2: July 06th**
    6 teams compete over 5 hours in 5 dungeons ({', '.join(dungeons[:5])})\n
    **Day 3: July 07th**
    6 teams compete over 5 hours in 6 dungeons ({', '.join(dungeons)})
        """

        # Prepare the initial embed for infos section
        embed_infos = Embed(
            title=infos["name"],
            description=infos_str,
            color=0xE04747,
            thumbnail=infos["icon"],
            footer="Source: Liquipedia",
        )

        # Prepare the initial embed for data section
        embed_data = Embed(
            title=infos["name"],
            color=0xE04747,
            footer="Source: Raider.io",
            timestamp=datetime.now(),
        )

        # Function to split data into chunks
        def chunk_data(data_list, chunk_size=1024):
            chunks = []
            current_chunk = "```ansi\n"
            for item in data_list:
                if len(current_chunk) + len(item) + 1 > chunk_size:
                    current_chunk += "```"
                    chunks.append(current_chunk)
                    current_chunk = "```ansi\n"
                current_chunk += item + "\n"
            if current_chunk != "```ansi\n":
                current_chunk += "```"
                chunks.append(current_chunk)
            return chunks

        # Chunk and add fields for safe_teams
        safe_team_chunks = chunk_data(safe_teams)
        for index, chunk in enumerate(safe_team_chunks):
            embed_data.add_field(
                name=f"Classement" if index == 0 else "\u200b", value=chunk
            )

        # Chunk and add fields for in_danger_teams
        in_danger_chunks = chunk_data(
            in_danger_teams
        )  # Wrap in list since it's a single item
        for index, chunk in enumerate(in_danger_chunks):
            embed_data.add_field(
                name="Équipe(s) en danger" if index == 0 else "\u200b", value=chunk
            )

        # Chunk and add fields for out_teams
        out_team_chunks = chunk_data(out_teams)
        for index, chunk in enumerate(out_team_chunks):
            embed_data.add_field(
                name="Équipe(s) éliminée(s)" if index == 0 else "\u200b", value=chunk
            )

        await self.wow_message.edit(
            content="<:MDRBelieve:973667607439892530>", embeds=[embed_infos, embed_data]
        )

    async def mdi_infos(self):
        tournament = "The_Great_Push/Dragonflight/Season_4/Global_Finals"
        tournament_data = await self.liquipedia_request(
            "worldofwarcraft",
            "tournament",
            f"[[pagename::{tournament}]]",
            query="startdate, enddate, name,prizepool,iconurl",
        )
        tournament_info = {
            "name": tournament_data["result"][0]["name"],
            "start_date": tournament_data["result"][0]["startdate"],
            "end_date": tournament_data["result"][0]["enddate"],
            "prizepool": tournament_data["result"][0]["prizepool"],
            "icon": tournament_data["result"][0]["iconurl"],
        }
        return tournament_info
