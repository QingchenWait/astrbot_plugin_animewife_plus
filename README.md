</div>

<div align="center">

# astrbot_plugin_animewife_plus

_✨ [AstrBot](https://github.com/AstrBotDevs/AstrBot) 群聊抽老婆插件 ✨_

[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](https://www.python.org/)
[![GitHub](https://img.shields.io/badge/作者-青尘工作室-blue)](https://space.bilibili.com/385556208/)

</div>


🪧 基于 [animewifex](https://github.com/monbed/astrbot_plugin_animewifex) 插件进行二次开发

- 添加**本地上传老婆池**功能，可以在插件 WebUI 中自由配置，支持老婆池列表自动化构建。
- 支持**自定义抽取结果文案**，支持使用通配符语法。
- 支持本地老婆池与在线老婆池之间**一键切换**。
- 去除全部 NTR 相关功能，**纯爱万岁**！



### 📥 配置在线版老婆池

本插件预置了与 [animewifex](https://github.com/monbed/astrbot_plugin_animewifex) 插件相同的默认在线老婆池。

如果部署 AstrBot 的服务器平台，能够正常访问 [GitHub 图床](https://github.com/monbed/wife)，则可保持默认设置不变。

| 配置项             | 默认配置                                            |
| :----------------- | --------------------------------------------------- |
| 图片服务器基础 URL | https://raw.githubusercontent.com/monbed/wife/main/ |
| 图片列表 URL       | https://animewife.dpdns.org/list.txt                |

如果网络不佳，也可以手动为在线老婆池下载图片缓存，放入 `AstrBot\data\plugin_data\astrbot_plugin_animewife_plus\img\wife` 目录。

### 🎯 配置本地版老婆池

可以通过 WebUI，在 插件配置项“本地老婆池图片集” 中，上传符合规则的图片。

- 图片文件名必须为 `<老婆来源>!<老婆名字>.jpg`(例如：`游戏人生!白.png`)，使用**半角 (英文) 感叹号**。
- 图片格式支持 jpg / jpeg / png / gif / bmp / webp。
- (**可选**) 上传一个 "指定本地老婆池中的哪些老婆，会加入卡池" 的文档，文件名必须为 `list.txt`。每行填写一个计划加入卡池的老婆的**完整图片文件名**。
- 如果没有上传 `list.txt` 文件，则默认使用本地老婆池中的全部符合标准的图片。

### 🛠️ 增删 & 切换老婆池

- 在 WebUI 中，可以灵活地 添加 / 删除 / 切换本地老婆池，卡池数据会实时同步。
- **需要注意**：为了避免老婆池切换/增删导致的冲突，每次修改配置后，插件都会自动清除当天的抽老婆记录。

### ✒️ 自定义抽老婆文案

- 可在 WebUI 中编写任意抽老婆结果文案。

- 支持通配符，语法为：

  | 通配符        | 含义                   |
  | ------------- | ---------------------- |
  | `<user_name>` | 用户在 QQ 群聊中的昵称 |
  | `<place>`     | 老婆来源               |
  | `<wife_name>` | 老婆名字               |

### 🔎 指令 ###

- `老婆帮助` 显示所有命令帮助
- `抽老婆` 每天一次，随机抽一张二次元老婆
- `查老婆` 查看今日老婆 加@可以查看别人老婆（支持不@昵称匹配）
- `换老婆` 重新抽取老婆
- `重置换` 重置换老婆次数，失败禁言，AstrBot 管理员权限不受限制

### ❤️ 致谢
- [astrbot_plugin_animewifex](https://github.com/monbed/astrbot_plugin_animewifex)
- [astrbot_plugin_AW](https://github.com/zgojin/astrbot_plugin_AW)
- [AstrBot](https://astrbot.app/)
