# ----------------------------------------------------------------------------
# Game catalog
# ----------------------------------------------------------------------------
# Each game carries:
#   key       stable identifier stored in servers.json
#   name      display name shown in the picker
#   note      shown in the editor (mod requirements, quirks, status)
#   buttons   locked command buttons rendered for every server of this game
#   sentinel  whether the client sends an empty marker packet after each command to find the reply's end
#   reply_ids_lag  whether command replies may carry the previous request's ID
#   ignore_event_packets  skips unsolicited type-1 event packets that are never command output
#
# Button fields:
#   label     button text
#   command   RCON command; "{arg}" pops an input box when clicked
#   confirm   optional confirmation message string for destructive actions
#   stops_server  marks a command that stops the server and its RCON connection
#   argmode   optional "underscore" to swap spaces for underscores in the arg
#
# Commands were verified per game. Only games with genuinely useful command
# sets are included.

GAME_CATALOG = [
    {
        "key": "ark_se", "name": "ARK: Survival Evolved",
        "sentinel": False,
        "note": "RCON needs RCONEnabled=True, RCONPort (default 27020) and "
                "ServerAdminPassword. Commands take no cheat prefix. Get Chat shows "
                "chat since the last Get Chat. The server can take several minutes to "
                "start. RCON listens on the MultiHome address when one is set, "
                "otherwise on every address. The server writes the admin password "
                "into its log.",
        "buttons": [
            {"label": "List Players", "command": "ListPlayers"},
            {"label": "Save World", "command": "SaveWorld"},
            {"label": "Get Chat", "command": "GetChat"},
            {"label": "Broadcast", "command": "Broadcast {arg}"},
            {"label": "Server Message", "command": "ServerChat {arg}"},
            {"label": "Destroy Wild Dinos", "command": "DestroyWildDinos",
             "confirm": "Destroy all wild creatures on the map now?"},
            {"label": "Kick Player", "command": "KickPlayer {arg}"},
            {"label": "Ban Player", "command": "BanPlayer {arg}",
             "confirm": "Ban this player?"},
            {"label": "Unban Player", "command": "UnbanPlayer {arg}"},
            {"label": "Shutdown", "command": "DoExit",
             "confirm": "Shut the server down now?", "stops_server": True},
        ],
    },
    {
        "key": "ark_asa", "name": "ARK: Survival Ascended",
        "sentinel": False,
        "note": "RCON needs RCONEnabled=True, RCONPort (default 27020) and "
                "ServerAdminPassword. Commands take no cheat prefix. Get Chat shows "
                "chat since the last Get Chat. For about 30 seconds after the port "
                "opens, Connect times out. RCON listens on every network address and "
                "the server cannot limit it, so keep the port closed to the internet.",
        "buttons": [
            {"label": "List Players", "command": "ListPlayers"},
            {"label": "Save World", "command": "SaveWorld"},
            {"label": "Get Chat", "command": "GetChat"},
            {"label": "Broadcast", "command": "Broadcast {arg}"},
            {"label": "Server Message", "command": "ServerChat {arg}"},
            {"label": "Destroy Wild Dinos", "command": "DestroyWildDinos",
             "confirm": "Destroy all wild creatures on the map now?"},
            {"label": "Kick Player", "command": "KickPlayer {arg}"},
            {"label": "Ban Player", "command": "BanPlayer {arg}",
             "confirm": "Ban this player?"},
            {"label": "Unban Player", "command": "UnbanPlayer {arg}"},
            {"label": "Shutdown", "command": "DoExit",
             "confirm": "Shut the server down now?", "stops_server": True},
        ],
    },
    {
        "key": "conan", "name": "Conan Exiles",
        "sentinel": False,
        "reply_ids_lag": True,
        "note": "Kick takes a player name then a reason. Ban takes a platform ID "
                "(from List Players) then a reason. Unban takes the ID. Kick and "
                "Ban work only on players who are online. For about 20 seconds "
                "after the server starts, commands fail while the map loads.",
        "buttons": [
            {"label": "List Players", "command": "listplayers"},
            {"label": "Kick", "command": "kickplayer name {arg}"},
            {"label": "Ban", "command": "banplayer platformid {arg}", "confirm": "Ban this player?"},
            {"label": "Unban", "command": "unbanplayer {arg}"},
            {"label": "List Bans", "command": "listbans"},
            {"label": "Broadcast", "command": "broadcast {arg}"},
            {"label": "Help", "command": "help"},
        ],
    },
    {
        "key": "palworld", "name": "Palworld",
        "note": "Commands are case-sensitive. Broadcast cannot contain spaces "
                "(spaces are auto-converted to underscores).",
        "buttons": [
            {"label": "Show Players", "command": "ShowPlayers"},
            {"label": "Info", "command": "Info"},
            {"label": "Save", "command": "Save"},
            {"label": "Broadcast", "command": "Broadcast {arg}", "argmode": "underscore"},
            {"label": "Kick Player", "command": "KickPlayer {arg}"},
            {"label": "Ban Player", "command": "BanPlayer {arg}",
             "confirm": "Ban this player (SteamID)?"},
            {"label": "Shutdown 60s", "command": "Shutdown 60 Restarting",
             "confirm": "Begin a 60-second shutdown?"},
            {"label": "Force Stop", "command": "DoExit",
             "confirm": "Force the server to stop now?", "stops_server": True},
        ],
    },
    {
        "key": "minecraft", "name": "Minecraft (Java)",
        "sentinel": False,
        "note": "Standard Java-edition console commands.",
        "buttons": [
            {"label": "List", "command": "list"},
            {"label": "Say", "command": "say {arg}"},
            {"label": "Save All", "command": "save-all"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Ban", "command": "ban {arg}", "confirm": "Ban this player?"},
            {"label": "Ban List", "command": "banlist"},
            {"label": "Weather Clear", "command": "weather clear"},
            {"label": "Set Day", "command": "time set day"},
            {"label": "Stop Server", "command": "stop", "confirm": "Stop the server?",
             "stops_server": True},
        ],
    },
    {
        "key": "cs2", "name": "Counter-Strike 2",
        "sentinel": False,
        "note": "RCON stays off unless the server starts with -usercon. Set the "
                "password with rcon_password. Default port 27015, the same as the "
                "game port. Kick takes a player's name as Status shows it.",
        "buttons": [
            {"label": "Status", "command": "status"},
            {"label": "Say", "command": "say {arg}"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Change Level", "command": "changelevel {arg}"},
        ],
    },
    {
        "key": "pz", "name": "Project Zomboid",
        "note": ("No leading slash over RCON. Put quotes around multi-word names. RCON stays off "
                 "until RCONPassword is set in the server's .ini (port 27015 by default). "
                 "Kick needs the player online."),
        "buttons": [
            {"label": "Players", "command": "players"},
            {"label": "Save", "command": "save"},
            {"label": "Server Message", "command": 'servermsg "{arg}"'},
            {"label": "Kick User", "command": "kickuser {arg}"},
            {"label": "Ban User", "command": "banuser {arg}", "confirm": "Ban this user?"},
            {"label": "Unban User", "command": "unbanuser {arg}"},
            {"label": "Show Options", "command": "showoptions"},
            {"label": "Save & Quit", "command": "quit", "confirm": "Save and stop the server?",
             "stops_server": True},
        ],
    },
    {
        "key": "squad", "name": "Squad",
        "sentinel": False,
        "ignore_event_packets": True,
        "note": "Default RCON port 21114. RCON stays off until Password is set "
                "in ServerConfig/Rcon.cfg; it listens on every address unless IP "
                "is set there. For about 10 s after a start, commands get no "
                "reply. Kick by ID takes the player's ID from List Players, then "
                "a reason (for example: 12 Breaking rules). A mistyped command "
                "gets no reply.",
        "buttons": [
            {"label": "List Players", "command": "ListPlayers"},
            {"label": "List Squads", "command": "ListSquads"},
            {"label": "Show Next Map", "command": "ShowNextMap"},
            {"label": "Broadcast", "command": "AdminBroadcast {arg}"},
            {"label": "Kick by ID", "command": "AdminKickById {arg}"},
            {"label": "Restart Match", "command": "AdminRestartMatch",
             "confirm": "Restart the current match?"},
            {"label": "End Match", "command": "AdminEndMatch",
             "confirm": "End the current match?"},
        ],
    },
    {
        "key": "gmod", "name": "Garry's Mod",
        "note": "Default port 27015, the same as the game port. Set the RCON "
                "password with rcon_password. Kick takes a player's name as "
                "Status shows it.",
        "buttons": [
            {"label": "Status", "command": "status"},
            {"label": "Say", "command": "say {arg}"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Change Level", "command": "changelevel {arg}"},
        ],
    },
    {
        "key": "eco", "name": "Eco",
        "sentinel": False,
        "note": "Default port 3002. RCON stays off until RconPassword is set in "
                "Configs/Network.eco. Kick/Ban use a Name,Reason format; banning "
                "a player who never joined needs their Steam or SLG ID. Very "
                "long replies (such as the full help list) make Eco drop the "
                "connection.",
        "buttons": [
            {"label": "Online Players", "command": "manage players"},
            {"label": "Save", "command": "manage save"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Ban", "command": "ban {arg}",
             "confirm": "Ban this player? (format: Name,Reason)"},
            {"label": "Ban List", "command": "ban"},
            {"label": "Unban", "command": "unban {arg}"},
        ],
    },
    {
        "key": "valheim", "name": "Valheim",
        "sentinel": False,
        "note": "Requires the ValheimRcon mod. Crossplay must be off "
                "(BepInEx mods do not load with crossplay on). On a new world, "
                "RCON opens only after the server is shut down cleanly and "
                "started again once.",
        "buttons": [
            {"label": "Save", "command": "save"},
            {"label": "Kick", "command": "kick {arg}"},
            {"label": "Ban", "command": "ban {arg}", "confirm": "Ban this player?"},
            {"label": "Unban", "command": "unban {arg}"},
            {"label": "List Bans", "command": "banlist"},
        ],
    },
]

CATALOG_BY_KEY = {g["key"]: g for g in GAME_CATALOG}


def game_uses_sentinel(game_key):
    g = CATALOG_BY_KEY.get(game_key)
    return g.get("sentinel", True) if g else True


def game_reply_ids_lag(game_key):
    g = CATALOG_BY_KEY.get(game_key)
    return g.get("reply_ids_lag", False) if g else False


def game_ignores_event_packets(game_key):
    g = CATALOG_BY_KEY.get(game_key)
    return g.get("ignore_event_packets", False) if g else False
