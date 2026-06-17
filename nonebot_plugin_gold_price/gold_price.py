import sqlite3
import asyncio
import base64
import io
import re
from datetime import datetime, timedelta
from os import path as os_path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import aiohttp
import matplotlib.dates as mdates
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.ticker import FixedLocator

from nonebot import on_command, get_bot, require
from nonebot.adapters import Message
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_localstore")
from nonebot_plugin_apscheduler import scheduler
import nonebot_plugin_localstore as store
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

from .config import plugin_config

__plugin_meta__ = PluginMetadata(
    name="金价查询",
    description="查询实时金价及价格走势",
    usage="/goldprice 或 /金价",
)

gold_price_cmd = on_command("goldprice", aliases={"金价"}, priority=5)

API_URL = "https://v3.alapi.cn/api/gold"

MARKET_DISPLAY = {"SH": "上海黄金交易所", "LF": "实时黄金价格"}

PALETTE = {
    "SH": "#1f3a93",
    "LF": "#e67e22",
    "fallback": ["#1f3a93", "#e67e22", "#27ae60", "#c0392b"],
}

MarketResult = Union[Dict[str, object], str]
ChartSeries = Dict[str, Tuple[Sequence[float], Sequence[str]]]


# ----- 数据层 ----------------------------------------------------------------


class DBManager:
    """SQLite 连接的上下文管理器，进入时自动建表"""

    def __init__(self) -> None:
        self.db_path = str(store.get_plugin_data_file("gold_price.db"))

    def __enter__(self) -> sqlite3.Connection:
        self.conn = sqlite3.connect(self.db_path)
        self._migrate()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS gold_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                price REAL NOT NULL,
                unit TEXT NOT NULL,
                time TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL
            )"""
        )
        self.conn.commit()


async def fetch_market_data(market: str) -> MarketResult:
    """拉取指定市场的实时金价，返回 dict 或错误字符串"""
    if not plugin_config.gold_api_token:
        return "API_TOKEN未配置"
    try:
        params = {"token": plugin_config.gold_api_token, "market": market}
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(API_URL, params=params) as response:
                data = await response.json()

        if data.get("code") != 200:
            return f"{market} API错误: {data.get('msg', '未知错误')}"

        target_symbol = "SH_AuTD" if market == "SH" else "Au"
        for item in data.get("data", []):
            if item.get("symbol") == target_symbol:
                return {
                    "market": market,
                    "symbol": item["symbol"],
                    "buy_price": item["buy_price"],
                    "sell_price": item["sell_price"],
                    "name": item["name"],
                }
        return f"{market} 未找到目标品种"
    except Exception as e:
        logger.opt(exception=e).debug(f"获取 {market} 金价失败")
        return f"{market} 请求失败: {e}"


def save_price_record(conn: sqlite3.Connection, data: Dict[str, object]) -> None:
    """落库当前价，0 值降级为最近一次非零价、并对完全相同记录去重"""
    if not isinstance(data, dict):
        return

    market = data["market"]
    symbol = data["symbol"]
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    buy_price = float(data.get("buy_price", 0))

    cursor = conn.cursor()

    if buy_price <= 0:
        cursor.execute(
            "SELECT price FROM gold_prices "
            "WHERE market = ? AND symbol = ? AND price > 0 "
            "ORDER BY time DESC LIMIT 1",
            (market, symbol),
        )
        result = cursor.fetchone()
        if not result:
            return
        buy_price = result[0]

    cursor.execute(
        "SELECT id FROM gold_prices "
        "WHERE market = ? AND symbol = ? AND price = ? AND time = ?",
        (market, symbol, buy_price, current_time),
    )
    if cursor.fetchone():
        return

    cursor.execute(
        "INSERT INTO gold_prices (price, unit, time, market, symbol) VALUES (?, ?, ?, ?, ?)",
        (buy_price, "元/克", current_time, market, symbol),
    )
    conn.commit()


def get_history_data(
    conn: sqlite3.Connection, market: str, days: int
) -> List[Tuple[float, str]]:
    """返回市场近 N 天的 (price, time) 列表，0 值用上一条有效价格填充"""
    cursor = conn.cursor()
    end = datetime.now()
    start = end - timedelta(days=days)
    cursor.execute(
        "SELECT price, time FROM gold_prices "
        "WHERE market = ? AND time BETWEEN ? AND ? "
        "ORDER BY time ASC",
        (
            market,
            start.strftime("%Y-%m-%d 00:00:00"),
            end.strftime("%Y-%m-%d 23:59:59"),
        ),
    )
    raw_data = cursor.fetchall()

    processed: List[Tuple[float, str]] = []
    last_valid: Optional[float] = None
    for price, timestamp in raw_data:
        if price > 0:
            last_valid = price
            processed.append((price, timestamp))
        elif last_valid is not None:
            processed.append((last_valid, timestamp))
    return processed


# ----- 图表层 ----------------------------------------------------------------


def resolve_chart_font(font_conf: str) -> FontProperties:
    """解析配置项返回 FontProperties，不修改 matplotlib 全局状态"""
    if font_conf:
        if os_path.isfile(font_conf):
            try:
                return FontProperties(fname=font_conf)
            except Exception as e:
                logger.debug(f"加载字体文件失败: {font_conf} ({e})")
        else:
            try:
                font_path = font_manager.findfont(
                    font_conf, fallback_to_default=False
                )
                return FontProperties(fname=font_path)
            except Exception as e:
                logger.debug(f"按名称查找字体失败: {font_conf} ({e})")

    for candidate in (
        "WenQuanYi Micro Hei",
        "Noto Sans CJK SC",
        "Microsoft YaHei",
        "SimHei",
    ):
        try:
            font_path = font_manager.findfont(candidate, fallback_to_default=False)
            return FontProperties(fname=font_path)
        except Exception:
            continue
    return FontProperties()


def _apply_font(items: Iterable, font_prop: FontProperties) -> None:
    for item in items:
        item.set_fontproperties(font_prop)


def _annotate_extreme(
    ax,
    times: Sequence[datetime],
    prices: Sequence[float],
    color: str,
    font_prop: FontProperties,
) -> None:
    """在折线上标注最新价、最高价、最低价"""
    if not prices:
        return
    max_idx = prices.index(max(prices))
    min_idx = prices.index(min(prices))
    latest_idx = len(prices) - 1
    seen = set()
    for idx, label in (
        (max_idx, f"高 {prices[max_idx]:.2f}"),
        (min_idx, f"低 {prices[min_idx]:.2f}"),
        (latest_idx, f"现 {prices[latest_idx]:.2f}"),
    ):
        if idx in seen:
            continue
        seen.add(idx)
        ax.annotate(
            label,
            xy=(times[idx], prices[idx]),
            xytext=(6, 8),
            textcoords="offset points",
            fontproperties=font_prop,
            fontsize=9,
            color=color,
            bbox=dict(
                boxstyle="round,pad=0.25",
                fc="white",
                ec=color,
                lw=0.6,
                alpha=0.85,
            ),
        )


def generate_chart(data_dict: ChartSeries, days: int) -> bytes:
    """渲染现代风格的价格走势图，返回 PNG 字节，不写盘也不依赖全局状态"""
    font_prop = resolve_chart_font(plugin_config.gold_chart_font)
    title_prop = FontProperties(
        fname=font_prop.get_file(), size=15, weight="bold"
    ) if font_prop.get_file() else FontProperties(size=15, weight="bold")

    fig = Figure(figsize=(12, 6), dpi=200, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_facecolor("#fafafa")

    all_times: List[datetime] = []
    for prices, timestamps in data_dict.values():
        if prices:
            all_times.extend(
                datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") for ts in timestamps
            )

    if all_times:
        min_time, max_time = min(all_times), max(all_times)
        actual_days = max((max_time - min_time).days, 1)
    else:
        max_time = datetime.now()
        min_time = max_time - timedelta(days=days)
        actual_days = days

    unique_dates = sorted(
        {
            datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(
                hour=0, minute=0, second=0
            )
            for prices, timestamps in data_dict.values()
            if prices
            for ts in timestamps
        }
    )

    for idx, (market_name, (prices, timestamps)) in enumerate(data_dict.items()):
        if not prices:
            continue
        times = [datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") for ts in timestamps]
        market_key = next(
            (k for k, v in MARKET_DISPLAY.items() if v == market_name), None
        )
        color = PALETTE.get(market_key, PALETTE["fallback"][idx % 4])

        ax.plot(
            times,
            prices,
            color=color,
            linewidth=1.8,
            marker="o",
            markersize=3.5,
            markerfacecolor="white",
            markeredgewidth=1.2,
            markeredgecolor=color,
            label=market_name,
            zorder=3,
        )
        ax.fill_between(times, prices, min(prices), color=color, alpha=0.08, zorder=1)
        _annotate_extreme(ax, times, list(prices), color, font_prop)

    ax.set_title(
        f"黄金价格走势对比（近 {actual_days} 天）",
        fontproperties=title_prop,
        pad=14,
        loc="left",
        color="#222",
    )
    ax.set_xlabel("日期", fontproperties=font_prop, fontsize=11, color="#555")
    ax.set_ylabel("价格（元/克）", fontproperties=font_prop, fontsize=11, color="#555")

    ax.grid(True, axis="y", linestyle="--", linewidth=0.6, alpha=0.4, zorder=0)
    ax.grid(False, axis="x")
    for spine_name in ("top", "right"):
        ax.spines[spine_name].set_visible(False)
    for spine_name in ("left", "bottom"):
        ax.spines[spine_name].set_color("#bbb")
        ax.spines[spine_name].set_linewidth(0.8)
    ax.tick_params(colors="#666", which="both", length=3)

    ax.set_xlim(min_time, max_time)

    num_dates = len(unique_dates)
    if num_dates and num_dates <= 10:
        ax.xaxis.set_major_locator(FixedLocator(mdates.date2num(unique_dates)))
    elif 10 < num_dates <= 30:
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    else:
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())

    time_span = max_time - min_time
    if time_span.days > 180 or min_time.year != max_time.year:
        date_fmt = mdates.DateFormatter("%Y-%m")
    elif time_span.days > 7:
        date_fmt = mdates.DateFormatter("%m-%d")
    else:
        date_fmt = mdates.DateFormatter("%m-%d\n%H:%M")
    ax.xaxis.set_major_formatter(date_fmt)

    legend = ax.legend(
        loc="upper left",
        prop=font_prop,
        frameon=True,
        framealpha=0.9,
        facecolor="white",
        edgecolor="#ddd",
    )
    if legend:
        for text in legend.get_texts():
            text.set_color("#333")

    _apply_font(ax.get_xticklabels() + ax.get_yticklabels(), font_prop)
    for label in ax.get_xticklabels():
        label.set_rotation(35)
        label.set_ha("right")

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    return buf.getvalue()


# ----- 消息构建 --------------------------------------------------------------


def _build_text_report(valid_data: Sequence[Dict[str, object]]) -> str:
    segments = [
        f"【{MARKET_DISPLAY.get(d['market'], d['market'])} - {d['name']}】\n"
        f"▶ 买入价：{d['buy_price']}元/克\n"
        f"◀ 卖出价：{d['sell_price']}元/克"
        for d in valid_data
    ]
    segments.append(
        f"📅 更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return "\n\n".join(segments)


def _build_chart_series(
    conn: sqlite3.Connection,
    valid_data: Sequence[Dict[str, object]],
    days: int,
) -> ChartSeries:
    series: ChartSeries = {}
    for d in valid_data:
        history = get_history_data(conn, d["market"], days)
        if history:
            prices, timestamps = zip(*history)
            display = MARKET_DISPLAY.get(d["market"], d["market"])
            series[display] = (prices, timestamps)
    return series


def _build_alert(valid_data: Sequence[Dict[str, object]]) -> Optional[str]:
    sh = next((d for d in valid_data if d["market"] == "SH"), None)
    if not sh:
        return None
    price = float(sh["buy_price"])
    if price > plugin_config.gold_threshold_high:
        return (
            f"⚠️ SH市场预警：当前买入价{price}元已突破"
            f"{plugin_config.gold_threshold_high}！"
        )
    if price < plugin_config.gold_threshold_low:
        return (
            f"⚠️ SH市场预警：当前买入价{price}元已跌破"
            f"{plugin_config.gold_threshold_low}！"
        )
    return None


async def _dispatch_report(
    bot: Bot,
    group_id: int,
    text_msg: str,
    chart_bytes: Optional[bytes],
    alert_msg: Optional[str],
) -> None:
    """统一的群组推送流程：文本 → 图表 → 预警"""
    await bot.send_group_msg(group_id=group_id, message=MessageSegment.text(text_msg))
    if chart_bytes:
        img_base64 = base64.b64encode(chart_bytes).decode()
        await bot.send_group_msg(
            group_id=group_id,
            message=MessageSegment.image(f"base64://{img_base64}"),
        )
    if alert_msg:
        await bot.send_group_msg(
            group_id=group_id, message=MessageSegment.text(alert_msg)
        )


def _collect_valid(*results: MarketResult) -> Tuple[List[Dict[str, object]], List[str]]:
    valid: List[Dict[str, object]] = []
    errors: List[str] = []
    for r in results:
        if isinstance(r, dict):
            valid.append(r)
        elif isinstance(r, str):
            errors.append(r)
    return valid, errors


def _safe_get_bot() -> Optional[Bot]:
    try:
        return get_bot()  # type: ignore[return-value]
    except (ValueError, KeyError) as e:
        logger.warning(f"无法获取 bot 实例，跳过本次推送: {e}")
        return None


# ----- 命令与定时任务 --------------------------------------------------------


@gold_price_cmd.handle()
async def handle_query(event: GroupMessageEvent, args: Message = CommandArg()) -> None:
    if not plugin_config.gold_api_token:
        await gold_price_cmd.finish("请配置API_TOKEN！")

    arg = args.extract_plain_text().strip()
    days = plugin_config.gold_default_days

    if arg:
        match = re.match(r"^\s*(\d+\.?\d*)\s*(天|年)\s*$", arg)
        if not match:
            await gold_price_cmd.finish("参数格式错误，请使用类似'7天'或'1.5年'的格式")
        num = float(match.group(1))
        unit = match.group(2)
        total_days = num * 365 if unit == "年" else num
        days = max(1, int(round(total_days)))

    bot = _safe_get_bot()
    if bot is None:
        await gold_price_cmd.finish("机器人未连接，请稍后再试")

    with DBManager() as conn:
        sh_data = await fetch_market_data("SH")
        await asyncio.sleep(plugin_config.gold_api_interval)  # 错峰，避免触发频控
        lf_data = await fetch_market_data("LF")

        valid_data, errors = _collect_valid(sh_data, lf_data)
        for d in valid_data:
            save_price_record(conn, d)

        if valid_data:
            text_msg = _build_text_report(valid_data)
            series = _build_chart_series(conn, valid_data, days)
            chart_bytes = generate_chart(series, days) if series else None
            alert_msg = _build_alert(valid_data)
            await _dispatch_report(
                bot, event.group_id, text_msg, chart_bytes, alert_msg
            )

        if errors:
            await gold_price_cmd.send(
                MessageSegment.text("⚠️ 部分数据获取失败：\n" + "\n".join(errors))
            )


@scheduler.scheduled_job(
    "cron",
    hour=plugin_config.gold_schedule_hour,
    minute=plugin_config.gold_schedule_minute,
    id="daily_report",
)
async def daily_report() -> None:
    if not plugin_config.gold_api_token or not plugin_config.gold_target_groups:
        return

    bot = _safe_get_bot()
    if bot is None:
        return

    with DBManager() as conn:
        sh_data = await fetch_market_data("SH")
        await asyncio.sleep(plugin_config.gold_api_interval)
        lf_data = await fetch_market_data("LF")

        valid_data, _ = _collect_valid(sh_data, lf_data)
        for d in valid_data:
            save_price_record(conn, d)

        if not valid_data:
            return

        days = plugin_config.gold_default_days
        text_msg = _build_text_report(valid_data)
        series = _build_chart_series(conn, valid_data, days)
        chart_bytes = generate_chart(series, days) if series else None
        alert_msg = _build_alert(valid_data)

        for group_id in plugin_config.gold_target_groups:
            try:
                await _dispatch_report(
                    bot, int(group_id), text_msg, chart_bytes, alert_msg
                )
            except Exception as e:
                logger.opt(exception=e).warning(
                    f"向群 {group_id} 推送金价报告失败"
                )
