from interactions import (
    Task,
    OrTrigger,
    Extension,
    listen,
    Embed,
    Client,
    TimeTrigger,
    TimestampStyles,
)
from interactions.client.utils import timestamp_converter
from datetime import datetime, timedelta
from src import logutil
from src.utils import load_config, fetch
from bs4 import BeautifulSoup
import os

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleLiquipedia")


class LiquipediaScraperClass(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot

    @listen()
    async def on_startup(self):
        self.get_page_data.start()
        # await self.get_page_data()

    @Task.create(
        OrTrigger(
            *[
                TimeTrigger(hour=i, minute=j, utc=False)
                for i in [18, 19, 20, 21, 22, 23, 0]
                for j in [0, 15, 30, 45]
            ]
        )
    )
    async def get_page_data(self):
        try:
            channel = await self.bot.fetch_channel(module_config["liquipediaChannelId"])
            message = await channel.fetch_message(module_config["liquipediaMessageId"])
        except Exception as e:
            logger.error(f"Failed to fetch channel or message: {e}")
            return
        matches = await self.get_upcoming_matches()
        if matches is None:
            return

        embed = Embed(
            title="Prochains matchs de Mandatory",
            description="Source: [Liquipedia](https://liquipedia.net/valorant/Mandatory)",
            color=0xE04747,
            timestamp=datetime.now(),
            thumbnail="https://liquipedia.net/commons/images/d/d7/Mandatory_2022_allmode.png",
        )
        for match in matches:
            if match["date"] < datetime.now():
                embed.add_field(
                    name=f"<:zrtON:962320783038890054> {match['team1']} ({match['team1_tag']}) {match['score']} {match['team2']} ({match['team2_tag']}) en {match['format']}<:zrtON:962320783038890054>",
                    value=f"{match['tournament']}",
                )
            elif match["date"] - datetime.now() < timedelta(days=2):
                embed.add_field(
                    name=f"{match['team1']} ({match['team1_tag']}) vs {match['team2']} ({match['team2_tag']}) en {match['format']}",
                    value=f"{timestamp_converter(match['date']).format(TimestampStyles.RelativeTime)}\n{match['tournament']}",
                )
            else:
                embed.add_field(
                    name=f"{match['team1']} ({match['team1_tag']}) vs {match['team2']} ({match['team2_tag']}) en {match['format']}",
                    value=f"{timestamp_converter(match['date']).format(TimestampStyles.LongDateTime)}\n{match['tournament']}",
                    inline=False,
                )

        first_heading, standing_str_current, standing_str_last_week, standings = (
            await self.get_standings()
        )
        if standings is None:
            return

        embedClassement = Embed(
            title=f"Classement de {first_heading}",
            description=f"Source: [Liquipedia](https://liquipedia.net/valorant/VCL/2024/France/Split_2/Regular_Season)",
            color=0xE04747,
            timestamp=datetime.now(),
        )
        embedClassement.add_field(name="Semaine en cours", value=standing_str_current)
        embedClassement.add_field(
            name="Semaine précédente", value=standing_str_last_week
        )

        try:
            await message.edit(content="", embeds=[embed, embedClassement])
        except Exception as e:
            logger.error(f"Failed to edit message: {e}")

    async def get_upcoming_matches(self):
        page_title = "Mandatory"
        base_url = f"https://liquipedia.net/valorant/{page_title}"
        html_content = await fetch(base_url)
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, "html.parser")
        matches_infobox = soup.find("div", {"class": "fo-nttax-infobox panel"})
        if not matches_infobox:
            logger.error("Failed to find matches infobox.")
            return None

        upcoming_matches = matches_infobox.find_all(
            "table", {"class": "wikitable wikitable-striped infobox_matches_content"}
        )

        matches = []
        for table in upcoming_matches:
            try:
                team1_element = table.select_one(".team-left span")
                team1 = (
                    team1_element["data-highlightingclass"] if team1_element else None
                )
                team1_tag_element = table.select_one(".team-left .team-template-text a")
                team1_tag = (
                    team1_tag_element.text.strip() if team1_tag_element else None
                )

                score_element = table.find("td", class_="versus").find(
                    "div", style="line-height:1.1"
                )
                score = score_element.text.strip() if score_element else None

                team2_element = table.select_one(".team-right span")
                team2 = (
                    team2_element["data-highlightingclass"] if team2_element else None
                )
                team2_tag_element = table.select_one(
                    ".team-right .team-template-text a"
                )
                team2_tag = (
                    team2_tag_element.text.strip() if team2_tag_element else None
                )

                date_timestamp_element = table.find(
                    "span", {"class": "timer-object timer-object-countdown-only"}
                )
                date_timestamp = (
                    date_timestamp_element["data-timestamp"]
                    if date_timestamp_element
                    else None
                )
                date = (
                    datetime.fromtimestamp(int(date_timestamp))
                    if date_timestamp
                    else None
                )
                match_format_element = table.select_one("td.versus abbr")
                match_format = (
                    match_format_element.text.strip() if match_format_element else None
                )
                tournament_element = table.select_one(".tournament-text a")
                tournament = (
                    tournament_element.text.strip() if tournament_element else None
                )

                if team1 and team2:
                    matches.append(
                        {
                            "team1": team1,
                            "team1_tag": team1_tag,
                            "team2": team2,
                            "team2_tag": team2_tag,
                            "date": date,
                            "format": match_format,
                            "tournament": tournament,
                            "score": score,
                        }
                    )
            except Exception as e:
                logger.error(f"Error parsing match data: {e}")

        return matches

    async def get_standings(self):
        page_title = "VCL/2024/France/Split_2/Regular_Season"
        base_url = f"https://liquipedia.net/valorant/{page_title}"
        html_content = await fetch(base_url)
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, "html.parser")
        first_heading = (
            soup.select_one(".firstHeading").text
            if soup.select_one(".firstHeading")
            else "Standings"
        )

        table = soup.select_one(
            'table.wikitable.wikitable-bordered.grouptable[style="width:425px;margin:0px"]'
        )
        if not table:
            logger.error("Failed to find standings table.")
            return None

        team_rows = soup.select("tr[data-toggle-area-content]")
        if not team_rows:
            logger.error("No team rows found.")
            return None

        values = [int(row["data-toggle-area-content"]) for row in team_rows]
        max_value = max(values)
        last_week_value = max_value - 1
        logger.debug(f"Max value: {max_value}, Last week value: {last_week_value}")

        standings = {}
        standing_str_current = "```ansi\n"
        standing_str_last_week = "```ansi\n"

        for value in [max_value, last_week_value]:
            team_rows = table.select(f"tr[data-toggle-area-content='{value}']")
            logger.debug(
                f"Processing standings for value: {value} with {len(team_rows)} teams."
            )

            for row in team_rows:
                try:
                    cells = row.find_all("td")
                    standing_tag = row.find(
                        "th",
                        {"class": lambda x: x and "bg-" in x, "style": "width:16px"},
                    )
                    standing = (
                        standing_tag.text.strip().strip(".") if standing_tag else ""
                    )

                    if cells:
                        team_name = (
                            cells[0]
                            .find("span", class_="team-template-text")
                            .text.strip()
                        )
                        logger.debug(f"Team name: {team_name}")

                        overall_result = (
                            "0-0"
                            if cells[1].text.strip() == "-"
                            else cells[1].text.strip() or "0-0"
                        )
                        match_result = (
                            cells[2].text.strip() if cells[2].text.strip() else "0-0"
                        )
                        round_result = (
                            cells[3].text.strip() if cells[3].text.strip() else "0-0"
                        )
                        round_diff = (
                            cells[4].text.strip() if cells[4].text.strip() else "+0"
                        )

                        rank_change_up = cells[0].find(
                            "span", class_="group-table-rank-change-up"
                        )
                        rank_change_down = cells[0].find(
                            "span", class_="group-table-rank-change-down"
                        )

                        if rank_change_up:
                            evolution = (
                                f"\u001b[1;32m{rank_change_up.text.strip()}\u001b[0m"
                            )
                        elif rank_change_down:
                            evolution = (
                                f"\u001b[1;31m{rank_change_down.text.strip()}\u001b[0m"
                            )
                        else:
                            evolution = "\u001b[1;30m==\u001b[0m"

                        standings[team_name] = standings.get(team_name, {})
                        standings[team_name][f"standing_{value}"] = {
                            "standing": standing,
                            "overall_result": overall_result,
                            "match_result": match_result,
                            "round_result": round_result,
                            "round_diff": round_diff,
                            "evolution": evolution,
                        }
                        logger.debug(standings[team_name][f"standing_{value}"])

                        formatted_str = f"{standing:<1} {team_name:<14} ({overall_result:<3}) {evolution:<2} ({round_diff})\n"
                        if value == max_value:
                            standing_str_current += formatted_str
                        else:
                            standing_str_last_week += formatted_str
                except Exception as e:
                    logger.error(f"Error parsing standings data: {e}")

        standing_str_current += "```"
        standing_str_last_week += "```"

        return first_heading, standing_str_current, standing_str_last_week, standings
