from src.utils import fetch

def ms_to_time(ms, hours=False):
    total_hours = ms // (1000 * 60 * 60)
    ms = ms % (1000 * 60 * 60)
    minutes = ms // (1000 * 60)
    ms = ms % (1000 * 60)
    seconds = ms // 1000
    milliseconds = ms % 1000
    
    if hours:
        return f"{total_hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"
    else:
        return f"{minutes:02}:{seconds:02}.{milliseconds:03}"

async def get_table_data():
    url = "https://raider.io/api/mythic-plus/rankings/teams?region=world&page=0&eventId=1000078&season=season-df-4-tr"
    data = await fetch(url, "json")
    dungeons_url = "https://raider.io/api/events/tgp-dragonflight-season-4/brackets/group-a"
    dungeons_data = await fetch(dungeons_url, "json")

    # Create the dungeons dictionary dynamically from the fetched data
    dungeons = {dungeon["dungeon"]["id"]: dungeon["dungeon"]["short_name"] for dungeon in dungeons_data["bracket"]["dungeons"]}
    # Define the longest name in the dungeons dictionary
    longest_dungeon_name = max(len(name) for name in dungeons.values())
    # Get the dungeon names long list
    dungeons_str = [dungeon["dungeon"]["name"] for dungeon in dungeons_data["bracket"]["dungeons"]]
    # Initialize a list to store each team's table row
    table_rows = []

    # Process each team
    for team_data in data["rankings"]["rankedTeams"]:
        team_name = team_data["platoon"]["name"]
        overall_score = team_data["score"]
        rank = team_data["rank"]

        # Initialize an empty dictionary to store dungeon info
        dungeon_info = {dungeon_id: {"level": 0, "time": "", "clearTimeMs": 0} for dungeon_id in dungeons.keys()}

        # Process runs to get time in mm:ss.cent format
        for run in team_data["runs"]:
            zone_id = run["zoneId"]
            if zone_id in dungeon_info:
                time_in_ms = run["clearTimeMs"]
                level = run["mythicLevel"]
                time_str = ms_to_time(time_in_ms)
                numchest = run["numChests"]
                dungeon_info[zone_id] = {
                    "level": level,
                    "time": time_str,
                    "clearTimeMs": time_in_ms,
                    "numChests": numchest,
                }

        # Calculate total time
        total_time_ms = sum(
            info["clearTimeMs"] for info in dungeon_info.values() if info["clearTimeMs"] > 0
        )
        total_time_str = ms_to_time(total_time_ms, True)
        if rank == 5:
            color = "33"
        elif rank == 6:
            color = "31"
        else:
            color = "37"

        # Combine team overall info and dungeon details into a single string
        team_row = f"\u001b[1;{color}m{rank:1} | {team_name:15} | {int(overall_score):2} | {total_time_str:12} |\u001b[0m"
        for zone_id, dungeon_name in dungeons.items():
            dungeon = dungeon_info[zone_id]
            if dungeon["clearTimeMs"] > 0:
                team_row += f"\n\u001b[0;{color}m{'':5} | {dungeon_name:{longest_dungeon_name}} | {f'{dungeon['level']} (+{dungeon['numChests']})':7} | {dungeon['time']:9} |\u001b[0m"

        table_rows.append(team_row)

    return table_rows, dungeons_str

def ensure_six_elements(lst, fill_value="fill"):
    while len(lst) < 6:
        lst.append(fill_value)
    return lst