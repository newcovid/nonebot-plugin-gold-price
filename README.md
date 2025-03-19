<div align="center">
  <a href="https://v2.nonebot.dev/store"><img src="https://github.com/A-kirami/nonebot-plugin-template/blob/resources/nbp_logo.png" width="180" height="180" alt="NoneBotPluginLogo"></a>
  <br>
  <p><img src="https://github.com/A-kirami/nonebot-plugin-template/blob/resources/NoneBotPlugin.svg" width="240" alt="NoneBotPluginText"></p>
</div>

<div align="center">

# nonebot-plugin-gold-price

_✨ 查询实时金价 ✨_


<a href="./LICENSE">
    <img src="https://img.shields.io/github/license/newcovid/nonebot-plugin-gold-price.svg" alt="license">
</a>
<a href="https://pypi.python.org/pypi/nonebot-plugin-gold-price">
    <img src="https://img.shields.io/pypi/v/nonebot-plugin-gold-price.svg" alt="pypi">
</a>
<img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="python">

</div>


## 📖 介绍

查询实时金价；存储查询时的金价到数据库中；绘制历史价格走势对比图；设置价格预警阈值（没什么用）；定时推送群聊金价。

## 💿 安装

<details open>
<summary>使用 nb-cli 安装</summary>
在 nonebot2 项目的根目录下打开命令行, 输入以下指令即可安装

    nb plugin install nonebot-plugin-gold-price

</details>

<details>
<summary>使用包管理器安装</summary>
在 nonebot2 项目的插件目录下, 打开命令行, 根据你使用的包管理器, 输入相应的安装命令

<details>
<summary>pip</summary>

    pip install nonebot-plugin-gold-price
</details>
<details>
<summary>pdm</summary>

    pdm add nonebot-plugin-gold-price
</details>
<details>
<summary>poetry</summary>

    poetry add nonebot-plugin-gold-price
</details>
<details>
<summary>conda</summary>

    conda install nonebot-plugin-gold-price
</details>

打开 nonebot2 项目根目录下的 `pyproject.toml` 文件, 在 `[tool.nonebot]` 部分追加写入

    plugins = ["nonebot_plugin_gold_price"]

</details>

## ⚙️ 配置

在 nonebot2 项目的`.env`文件中添加下表中的必填配置

|        配置项        | 必填  | 默认值 |                                     说明                                     |
| :------------------: | :---: | :----: | :--------------------------------------------------------------------------: |
|    gold_api_token    |  是   |   无   | 金价查询的API令牌，[TOKEN申请地址](https://www.alapi.cn/api/71/api_document) |
|  gold_api_interval   |  否   |   1    |                               API请求间隔(秒)                                |
|  gold_default_days   |  否   |   30   |                               默认查询历史天数                               |
| gold_threshold_high  |  否   |  800   |                                  上预警阈值                                  |
|  gold_threshold_low  |  否   |  650   |                                  下预警阈值                                  |
|  gold_target_groups  |  否   |   无   |                     定时推送的群号列表，例["123", "456"]                     |
|  gold_schedule_hour  |  否   |   18   |                                定时任务的小时                                |
| gold_schedule_minute |  否   |   30   |                                定时任务的分钟                                |

## 🎉 使用
### 指令表
|   指令   | 权限  | 需要@ | 范围  |                            说明                            |
| :------: | :---: | :---: | :---: | :--------------------------------------------------------: |
|   金价   | 群员  |  否   | 群聊  |      查询实时金价，并返回默认查询历史天数的价格折线图      |
| 金价 x天 | 群员  |  否   | 群聊  |          查询实时金价，并返回历史x天的价格折线图           |
| 金价 x年 | 群员  |  否   | 群聊  | 查询实时金价，并返回历史x年的价格折线图，可为浮点数，例0.1 |
### 效果图
![image1](https://github.com/newcovid/nonebot-plugin-gold-price/blob/master/images/rendering_1.png)
![image2](https://github.com/newcovid/nonebot-plugin-gold-price/blob/master/images/rendering_2.png)

