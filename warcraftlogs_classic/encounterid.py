ENCOUNTER_ID = {
    # Mogu'shan Vaults
    1395: "The Stone Guard",
    1390: "Feng the Accursed",
    1434: "Gara'jal the Spiritbinder",
    1436: "The Spirit Kings",
    1500: "Elegon",
    1407: "Will of the Emperor",

    # Heart of Fear
    1549: "Imperial Vizier Zor'lok",
    1550: "Blade Lord Ta'yak",
    1551: "Garalon",
    1552: "Wind Lord Mel'jarak",
    1553: "Amber-Shaper Un'sok",
    1554: "Grand Empress Shek'zeer",

    # Terrace of Endless Spring
    1409: "Protectors of the Endless",
    1505: "Tsulong",
    1506: "Lei Shi",
    1507: "Sha of Fear",

    # Throne of Thunder
    1577: "Jin'rokh the Breaker",
    1575: "Horridon",
    1570: "Council of Elders",
    1565: "Tortos",
    1578: "Megaera",
    1573: "Ji-Kun",
    1572: "Durumu the Forgotten",
    1574: "Primordius",
    1576: "Dark Animus",
    1579: "Iron Qon",
    1560: "Twin Consorts",
    1580: "Lei Shen",
    1581: "Ra-den",

    # Siege of Orgrimmar
    1622: "Immerseus",
    1623: "The Fallen Protectors",
    1624: "Norushen",
    1625: "Sha of Pride",
    1627: "Galakras",
    1628: "Iron Juggernaut",
    1629: "Kor'kron Dark Shaman",
    1630: "General Nazgrim",
    1631: "Malkorok",
    1632: "Spoils of Pandaria",
    1633: "Thok the Bloodthirsty",
    1634: "Siegecrafter Blackfuse",
    1635: "Paragons of the Klaxxi",
    1636: "Garrosh Hellscream",
}

ENCOUNTER_ZONES = {
    "Mogu'shan Vaults": [1395, 1390, 1434, 1436, 1500, 1407],
    "Heart of Fear": [1549, 1550, 1551, 1552, 1553, 1554],
    "Terrace of Endless Spring": [1409, 1505, 1506, 1507],
    "Throne of Thunder": [1577, 1575, 1570, 1565, 1578, 1573, 1572, 1574, 1576, 1579, 1560, 1580, 1581],
    "Siege of Orgrimmar": [1622, 1623, 1624, 1625, 1627, 1628, 1629, 1630, 1631, 1632, 1633, 1634, 1635, 1636],
}

# Zones are loaded live from the WCL API (worldData.zones): the former
# ZONES_BY_ID / ZONES_BY_SHORT_NAME ids were Blizzard instance ids, not WCL zone
# ids. Only the short names are kept, as case-insensitive aliases that are
# resolved against the live zone names.
ZONE_ALIASES = {
    "msv": "Mogu'shan Vaults",
    "hof": "Heart of Fear",
    "toes": "Terrace of Endless Spring",
    "tot": "Throne of Thunder",
    "soo": "Siege of Orgrimmar",
}

DIFFICULTIES = {
    5: "MYTHIC", # Only SoO
    4: "HEROIC",
    3: "NORMAL",
    1: "LFR",
}
