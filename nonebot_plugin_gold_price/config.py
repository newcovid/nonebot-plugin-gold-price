from pydantic import BaseModel, Field
from nonebot import get_plugin_config
from typing import List


class GoldPriceConfig(BaseModel):
    gold_api_interval: int = Field(default=1, description="API请求间隔(秒)")
    gold_api_token: str = Field(default="", description="API令牌，必填")
    gold_target_groups: List[str] = Field(
        default_factory=list, description="定时推送的群号列表"
    )
    gold_threshold_high: float = Field(default=800.0, description="上预警阈值")
    gold_threshold_low: float = Field(default=650.0, description="下预警阈值")
    gold_default_days: int = Field(default=30, description="默认查询天数")
    gold_schedule_hour: int = Field(default=18, description="定时任务的小时")
    gold_schedule_minute: int = Field(default=30, description="定时任务的分钟")


plugin_config = get_plugin_config(GoldPriceConfig)
