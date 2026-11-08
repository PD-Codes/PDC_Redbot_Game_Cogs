from redbot.core.i18n import Translator
from redbot.core.utils.chat_formatting import humanize_number

_ = Translator("WoWToolsForever", __file__)


def format_to_gold(price: int, emotes: dict = None) -> str:
    """
    Format an int to a WoW gold string with emotes.

    :param price:
    :param emotes:
    :return: String containing gold price and emotes if provided.
    """
    price = str(price)
    gold_text = ""
    silver_text = ""
    copper_text = ""

    gold_emoji = emotes["gold"] if emotes else None
    silver_emoji = emotes["silver"] if emotes else None
    copper_emoji = emotes["copper"] if emotes else None

    gold = humanize_number(int(price[:-4])) if price[:-4] else "00"
    if gold != "00":
        gold_text = f"{gold}g" if gold_emoji is None else gold + gold_emoji

    silver = price[-4:-2]
    if silver != "00":
        silver_text = f"{silver}s" if silver_emoji is None else silver + silver_emoji

    copper = price[-2:]
    if copper != "00":
        copper_text = f"{copper}c" if copper_emoji is None else copper + copper_emoji

    return gold_text + silver_text + copper_text
