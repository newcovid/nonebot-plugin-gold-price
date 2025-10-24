import sqlite3
import asyncio
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
from os import path as os_path
from nonebot import on_command, get_bot, require
from nonebot.adapters import Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
import aiohttp
import base64

require("nonebot_plugin_apscheduler")
require("nonebot_plugin_localstore")  # 声明依赖
from nonebot_plugin_apscheduler import scheduler
import nonebot_plugin_localstore as store  # 导入localstore
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment, Bot
from matplotlib.ticker import FixedLocator
from .config import plugin_config

# 插件元数据，用于描述插件的基本信息
__plugin_meta__ = PluginMetadata(
    name="金价查询",  # 插件名称
    description="查询实时金价及价格走势",  # 插件描述
    usage="/goldprice 或 /金价",  # 插件使用方法
)

# 注册一个命令处理器，支持命令 "/goldprice" 和 "/金价"
gold_price_cmd = on_command("goldprice", aliases={"金价"}, priority=5)

# API接口地址，用于获取黄金价格数据
API_URL = "https://v3.alapi.cn/api/gold"


class DBManager:
    """数据库管理类，用于管理SQLite数据库连接和操作"""

    def __init__(self):
        # 数据库文件路径
        self.db_path = str(store.get_plugin_data_file("gold_price.db"))

    def __enter__(self):
        # 进入上下文时，建立数据库连接并执行迁移
        self.conn = sqlite3.connect(self.db_path)
        self._migrate()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 退出上下文时，关闭数据库连接
        self.conn.close()

    def _migrate(self):
        """数据库迁移方法，确保表结构存在"""
        cursor = self.conn.cursor()
        cursor.execute(
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


async def fetch_market_data(market: str):
    """获取指定市场的黄金价格数据"""
    # 检查是否配置了API Token
    if not plugin_config.gold_api_token:
        return "API_TOKEN未配置"
        # 等待指定的间隔时间
    try:
        params = {"token": plugin_config.gold_api_token, "market": market}
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, params=params, timeout=10) as response:
                data = await response.json()

        if data.get("code") != 200:
            return f"{market} API错误: {data.get('msg', '未知错误')}"

        # 根据市场选择目标品种符号
        target_symbol = "SH_AuTD" if market == "SH" else "Au"
        for item in data.get("data", []):
            if item.get("symbol") == target_symbol:
                # 返回目标品种的相关数据
                return {
                    "market": market,
                    "symbol": item["symbol"],
                    "buy_price": item["buy_price"],
                    "sell_price": item["sell_price"],
                    "name": item["name"],
                }
        return f"{market} 未找到目标品种"
    except Exception as e:
        # 捕获异常并返回错误信息
        return f"{market} 请求失败: {str(e)}"


def save_price_record(conn, data):
    """存储黄金价格记录到数据库（自动处理0值并避免重复无效记录）"""
    if not isinstance(data, dict):
        return

    market = data["market"]
    symbol = data["symbol"]
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    buy_price = float(data.get("buy_price", 0))

    cursor = conn.cursor()

    # 如果当前价格为0，查找最近的非零记录
    if buy_price <= 0:
        cursor.execute(
            "SELECT price, time FROM gold_prices "
            "WHERE market = ? AND symbol = ? AND price > 0 "
            "ORDER BY time DESC LIMIT 1",
            (market, symbol),
        )
        result = cursor.fetchone()
        if not result:
            return  # 无历史有效数据，跳过保存
        buy_price = result[0]  # 使用最近的非零价格

    # 检查是否存在完全相同的记录（价格和时间均相同）
    cursor.execute(
        "SELECT id FROM gold_prices "
        "WHERE market = ? AND symbol = ? AND price = ? AND time = ?",
        (market, symbol, buy_price, current_time),
    )
    if cursor.fetchone():
        return  # 已存在相同记录，跳过插入

    # 插入新记录
    cursor.execute(
        "INSERT INTO gold_prices (price, unit, time, market, symbol) VALUES (?, ?, ?, ?, ?)",
        (buy_price, "元/克", current_time, market, symbol),
    )
    conn.commit()


def get_history_data(conn, market, days):
    """获取指定市场的历史价格数据"""
    cursor = conn.cursor()
    end = datetime.now()  # 当前时间
    start = end - timedelta(days=days)  # 起始时间
    cursor.execute(
        "SELECT price, time FROM gold_prices "
        "WHERE market = ? AND time BETWEEN ? AND ? "
        "ORDER BY time ASC",
        (
            market,
            start.strftime("%Y-%m-%d 00:00:00"),  # 起始时间格式化
            end.strftime("%Y-%m-%d 23:59:59"),  # 结束时间格式化
        ),
    )
    raw_data = cursor.fetchall()

    # 动态处理0值：用前一个有效值填充
    processed_data = []
    last_valid_price = None
    for price, timestamp in raw_data:
        if price > 0:
            last_valid_price = price
            processed_data.append((price, timestamp))
        elif last_valid_price is not None:
            processed_data.append((last_valid_price, timestamp))
        # 忽略初始连续0值（无有效数据时）

    return processed_data


def generate_chart(data_dict, filename, days):
    """生成黄金价格走势图"""

    set_chart_font(plugin_config.gold_chart_font)
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(12, 6))
    ax = fig.add_subplot(111)

    all_times = []
    for market_name, (prices, timestamps) in data_dict.items():
        if prices:
            times = [datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") for ts in timestamps]
            all_times.extend(times)

    if all_times:
        min_time = min(all_times)
        max_time = max(all_times)
        actual_days = (max_time - min_time).days
    else:
        max_time = datetime.now()
        min_time = max_time - timedelta(days=days)
        actual_days = days

    unique_dates = set()
    for market_name, (prices, timestamps) in data_dict.items():
        if prices:
            for ts in timestamps:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(
                    hour=0, minute=0, second=0
                )
                unique_dates.add(dt)
    unique_dates = sorted(unique_dates)
    num_dates = len(unique_dates)

    for market_name, (prices, timestamps) in data_dict.items():
        if not prices:
            continue
        times = [datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") for ts in timestamps]
        ax.plot(
            times,
            prices,
            marker="o",
            linestyle="-",
            linewidth=1.5,
            markersize=3,
            label=market_name,
        )

    font_path = plugin_config.gold_chart_font
    if os_path.isfile(font_path):
        font_prop = font_manager.FontProperties(fname=font_path)
    else:
        try:
            font_prop = font_manager.FontProperties(family="sans-serif")
        except:
            font_prop = None

    ax.set_title(
        f"黄金价格走势对比（近{actual_days}天）", fontsize=14, fontproperties=font_prop
    )
    ax.set_xlabel("日期", fontsize=12, fontproperties=font_prop)
    ax.set_ylabel("价格（元/克）", fontsize=12, fontproperties=font_prop)

    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", prop=font_prop)

    ax.set_xlim(min_time, max_time)

    if num_dates > 0:
        if num_dates <= 10:
            ax.xaxis.set_major_locator(FixedLocator(mdates.date2num(unique_dates)))
        elif 10 < num_dates <= 30:
            ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        else:
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
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
    plt.gcf().autofmt_xdate(rotation=45, ha="right")
    plt.tight_layout()

    plt.savefig(filename, dpi=300, bbox_inches="tight")
    plt.close()


async def send_price_report(conn, sh_data, lf_data, bot: Bot, days, group_id=None):
    """发送黄金价格报告到指定群组"""
    valid_data = {}
    if isinstance(sh_data, dict):
        valid_data["上海黄金交易所"] = sh_data
    if isinstance(lf_data, dict):
        valid_data["实时黄金价格"] = lf_data

    chart_data = {}
    for market_name, data in valid_data.items():
        history = get_history_data(conn, data["market"], days)
        if history:
            prices, timestamps = zip(*history)
            chart_data[market_name] = (prices, timestamps)

    chart_path = str(store.get_plugin_cache_file("gold_chart.png"))
    if chart_data:
        generate_chart(chart_data, chart_path, days)

    msg_segments = []
    for market_name, data in valid_data.items():
        msg_segments.append(
            f"【{market_name} - {data['name']}】\n"
            f"▶ 买入价：{data['buy_price']}元/克\n"
            f"◀ 卖出价：{data['sell_price']}元/克"
        )

    if not msg_segments:
        return

    msg_segments.append(f"📅 更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    final_msg = "\n\n".join(msg_segments)

    if group_id:
        await bot.send_group_msg(
            group_id=group_id, message=MessageSegment.text(final_msg)
        )
        if chart_data:
            with open(chart_path, "rb") as f:
                img_base64 = base64.b64encode(f.read()).decode()
            await bot.send_group_msg(
                group_id=group_id, message=MessageSegment.image(f"base64://{img_base64}")
            )

    if isinstance(sh_data, dict):
        price = float(sh_data["buy_price"])
        alert = ""
        if price > plugin_config.gold_threshold_high:
            alert = f"⚠️ SH市场预警：当前买入价{price}元已突破{plugin_config.gold_threshold_high}！"
        elif price < plugin_config.gold_threshold_low:
            alert = f"⚠️ SH市场预警：当前买入价{price}元已跌破{plugin_config.gold_threshold_low}！"
        if alert and group_id:
            await bot.send_group_msg(
                group_id=group_id, message=MessageSegment.text(alert)
            )


def set_chart_font(font_conf):
    """设置图表字体，增强跨平台兼容性"""
    # 重置字体设置
    plt.rcParams["font.family"] = ["sans-serif"]
    plt.rcParams["font.sans-serif"] = []
    plt.rcParams["axes.unicode_minus"] = False

    # 定义跨平台中文字体候选列表
    font_candidates = [
        "WenQuanYi Micro Hei",  # Linux 常见字体
        "Noto Sans CJK SC",  # Linux/部分 Windows
        "Microsoft YaHei",  # Windows 默认
        "SimHei",  # Windows 备用
        "sans-serif",  # 最终回退
    ]

    if font_conf and font_conf != "":
        # 用户自定义字体逻辑
        if os_path.isfile(font_conf):
            try:
                font_prop = font_manager.FontProperties(fname=font_conf)
                font_name = font_prop.get_name()
                plt.rcParams["font.sans-serif"] = [font_name]
                # 确保字体设置生效
                plt.rcParams["font.family"] = ["sans-serif"]
                return
            except Exception as e:
                pass
        else:
            try:
                font_path = font_manager.findfont(font_conf)
                font_prop = font_manager.FontProperties(fname=font_path)
                font_name = font_prop.get_name()
                plt.rcParams["font.sans-serif"] = [font_name]
                # 确保字体设置生效
                plt.rcParams["font.family"] = ["sans-serif"]
                return
            except Exception as e:
                pass

    # 未配置字体时，按候选列表尝试加载
    for font_name in font_candidates:
        try:
            # 强制查找字体文件路径，避免缓存问题
            font_path = font_manager.findfont(font_name, fallback_to_default=False)
            font_prop = font_manager.FontProperties(fname=font_path)
            plt.rcParams["font.sans-serif"] = [font_prop.get_name()]
            # 确保字体设置生效
            plt.rcParams["font.family"] = ["sans-serif"]
            return
        except Exception as e:
            continue

    # 所有候选均失败时，强制设置回退方案
    plt.rcParams["font.sans-serif"] = ["sans-serif"]
    plt.rcParams["font.family"] = ["sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False


@gold_price_cmd.handle()
async def handle_query(event: GroupMessageEvent, args: Message = CommandArg()):
    """处理用户查询命令"""
    if not plugin_config.gold_api_token:
        await gold_price_cmd.finish("请配置API_TOKEN！")
    arg = args.extract_plain_text().strip()
    days = plugin_config.gold_default_days

    if arg:
        import re

        match = re.match(r"^\s*(\d+\.?\d*)\s*(天|年)\s*$", arg)
        if match:
            num = float(match.group(1))
            unit = match.group(2)
            total_days = num * 365 if unit == "年" else num
            days = max(1, int(round(total_days)))
        else:
            await gold_price_cmd.finish("参数格式错误，请使用类似'7天'或'1.5年'的格式")

    bot = get_bot()
    with DBManager() as conn:
        # 并发获取数据
        sh_data = await fetch_market_data("SH")
        await asyncio.sleep(plugin_config.gold_api_interval)  # 等待间隔
        lf_data = await fetch_market_data("LF")

        errors = []
        for data in [sh_data, lf_data]:
            if isinstance(data, dict):
                save_price_record(conn, data)
            else:
                errors.append(data)

        await send_price_report(
            conn, sh_data, lf_data, bot, days, group_id=event.group_id
        )

        if errors:
            error_msg = "⚠️ 部分数据获取失败：\n" + "\n".join(errors)
            await gold_price_cmd.send(MessageSegment.text(error_msg))


@scheduler.scheduled_job(
    "cron",
    hour=plugin_config.gold_schedule_hour,
    minute=plugin_config.gold_schedule_minute,
    id="daily_report",
)
async def daily_report():
    """定时任务：每日推送黄金价格报告"""
    if not plugin_config.gold_api_token or not plugin_config.gold_target_groups:
        return

    bot = get_bot()
    with DBManager() as conn:
        # 并发获取数据
        sh_data = await fetch_market_data("SH")
        await asyncio.sleep(plugin_config.gold_api_interval)  # 等待间隔
        lf_data = await fetch_market_data("LF")

        # 统一保存数据
        valid_data = []
        for data in [sh_data, lf_data]:
            if isinstance(data, dict):
                save_price_record(conn, data)
                valid_data.append(data)

        # 只在有有效数据时进行推送
        if valid_data:
            # 生成统一消息内容
            chart_path = str(store.get_plugin_cache_file("gold_chart.png"))
            days = plugin_config.gold_default_days
            generate_chart_data = {}

            # 准备图表数据
            for data in valid_data:
                market_name = (
                    "上海黄金交易所" if data["market"] == "SH" else "实时黄金价格"
                )
                history = get_history_data(conn, data["market"], days)
                if history:
                    prices, timestamps = zip(*history)
                    generate_chart_data[market_name] = (prices, timestamps)

            if generate_chart_data:
                generate_chart(generate_chart_data, chart_path, days)

            # 构建统一消息
            msg_segments = []
            for data in valid_data:
                market_name = (
                    "上海黄金交易所" if data["market"] == "SH" else "实时黄金价格"
                )
                msg_segments.append(
                    f"【{market_name} - {data['name']}】\n"
                    f"▶ 买入价：{data['buy_price']}元/克\n"
                    f"◀ 卖出价：{data['sell_price']}元/克"
                )
            msg_segments.append(
                f"📅 更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            final_msg = "\n\n".join(msg_segments)

            # 统一推送所有群组
            for group_id in plugin_config.gold_target_groups:
                # 发送文本消息
                await bot.send_group_msg(
                    group_id=group_id, message=MessageSegment.text(final_msg)
                )

                # 发送图表
                if generate_chart_data:
                    with open(chart_path, "rb") as f:
                        img_base64 = base64.b64encode(f.read()).decode()
                    await bot.send_group_msg(
                        group_id=group_id,
                        message=MessageSegment.image(f"base64://{img_base64}"),
                    )

                # 发送预警
                if any(d["market"] == "SH" for d in valid_data):
                    sh_price = next(
                        d["buy_price"] for d in valid_data if d["market"] == "SH"
                    )
                    price = float(sh_price)
                    alert = ""
                    if price > plugin_config.gold_threshold_high:
                        alert = f"⚠️ SH市场预警：当前买入价{price}元已突破{plugin_config.gold_threshold_high}！"
                    elif price < plugin_config.gold_threshold_low:
                        alert = f"⚠️ SH市场预警：当前买入价{price}元已跌破{plugin_config.gold_threshold_low}！"
                    if alert:
                        await bot.send_group_msg(
                            group_id=group_id, message=MessageSegment.text(alert)
                        )
