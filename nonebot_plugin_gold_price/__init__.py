from nonebot.plugin import PluginMetadata
from .config import GoldPriceConfig
from .gold_price import *

__plugin_meta__ = PluginMetadata(
    name="金价查询",
    description="提供实时金价查询、定时推送、金价预警功能。另外能够将历史查询数据进行存储并绘制折线图表。数据来源于金价API",
    usage="/goldprice x天; /金价 x天;  /金价 x年",
    type="application",
    homepage="https://github.com/newcovid/nonebot-plugin-gold-price",
    config=GoldPriceConfig,
    supported_adapters={"~onebot.v11"},
)
