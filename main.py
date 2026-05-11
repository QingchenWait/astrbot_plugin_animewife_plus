from astrbot.api.all import *
from astrbot.api.star import StarTools
from astrbot.api.event import filter
from datetime import datetime, timedelta
import random
import os
import json
import aiohttp
import asyncio
import time
import imghdr
import logging
import hashlib
from typing import Optional

logger = logging.getLogger("astrbot")

# ==================== 常量定义 ====================

PLUGIN_DIR = StarTools.get_data_dir("astrbot_plugin_animewife_plus")
CONFIG_DIR = os.path.join(PLUGIN_DIR, "config")
IMG_DIR = os.path.join(PLUGIN_DIR, "img", "wife")
LOCAL_WIFE_POOL_DIR = os.path.join(PLUGIN_DIR, "files", "local_wife_pool_files")
LOCAL_WIFE_POOL_PREFIX = "local_wife_pool/"
LOCAL_WIFE_LIST_FILE = os.path.join(LOCAL_WIFE_POOL_DIR, "list.txt")
LOCAL_WIFE_SOURCE_LOCAL_FIRST = "优先从本地上传"
LOCAL_WIFE_SOURCE_ONLINE_FIRST = "优先从链接在线读取"
DEFAULT_WIFE_MESSAGE_TEMPLATE = "<user_name>，你今天的老婆是来自<place>的<wife_name>，请好好珍惜哦~"
SUPPORTED_LOCAL_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# 确保目录存在
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(IMG_DIR, exist_ok=True)
os.makedirs(LOCAL_WIFE_POOL_DIR, exist_ok=True)

# 数据文件路径
RECORDS_FILE = os.path.join(CONFIG_DIR, "records.json")
SWAP_REQUESTS_FILE = os.path.join(CONFIG_DIR, "swap_requests.json")
WIFE_LIST_CACHE_FILE = os.path.join(CONFIG_DIR, "wife_list_cache.txt")
RUNTIME_STATE_FILE = os.path.join(CONFIG_DIR, "runtime_state.json")

# ==================== 全局数据存储 ====================

records = {  # 统一的记录数据结构
    "change": {},     # 换老婆记录
    "reset": {},      # 重置使用次数
    "swap": {}        # 交换老婆请求次数
}
swap_requests = {}  # 交换请求数据

# ==================== 并发锁 ====================

config_locks = {}      # 群组配置锁


def get_config_lock(group_id: str) -> asyncio.Lock:
    """获取或创建群组配置锁"""
    if group_id not in config_locks:
        config_locks[group_id] = asyncio.Lock()
    return config_locks[group_id]

def get_today():
    """获取当前上海时区日期字符串"""
    utc_now = datetime.utcnow()
    return (utc_now + timedelta(hours=8)).date().isoformat()


def load_json(path: str) -> dict:
    """安全加载 JSON 文件"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def save_json(path: str, data: dict) -> None:
    """保存数据到 JSON 文件"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_group_config(group_id: str) -> dict:
    """加载群组配置"""
    return load_json(os.path.join(CONFIG_DIR, f"{group_id}.json"))


def save_group_config(group_id: str, config: dict) -> None:
    """保存群组配置"""
    save_json(os.path.join(CONFIG_DIR, f"{group_id}.json"), config)


def iter_group_config_files() -> list:
    """列出群组抽老婆配置文件，排除插件内部记录文件。"""
    reserved = {
        os.path.basename(RECORDS_FILE),
        os.path.basename(SWAP_REQUESTS_FILE),
        os.path.basename(WIFE_LIST_CACHE_FILE),
        os.path.basename(RUNTIME_STATE_FILE),
    }
    try:
        return [
            os.path.join(CONFIG_DIR, filename)
            for filename in os.listdir(CONFIG_DIR)
            if filename.endswith(".json") and filename not in reserved
        ]
    except Exception:
        return []


def is_valid_local_wife_filename(filename: str) -> bool:
    """检查本地老婆池文件名是否符合 <来源>!<名字>.<图片格式>。"""
    if not filename or filename != os.path.basename(filename):
        return False
    stem, ext = os.path.splitext(filename)
    if ext.lower() not in SUPPORTED_LOCAL_IMAGE_EXTS:
        return False
    if "!" not in stem:
        return False
    source, name = stem.split("!", 1)
    return bool(source.strip() and name.strip())


def is_readable_image(path: str) -> bool:
    """使用标准库快速验证图片可读性，避免把损坏文件写入列表。"""
    try:
        return os.path.isfile(path) and imghdr.what(path) is not None
    except Exception:
        return False


def validate_local_wife_filenames(filenames: list[str]) -> list[str]:
    """过滤出本地老婆池中真实存在、命名正确且可读取的图片文件。"""
    valid = []
    seen = set()
    for filename in filenames:
        filename = os.path.basename(str(filename).strip())
        if filename in seen or not is_valid_local_wife_filename(filename):
            continue
        path = os.path.join(LOCAL_WIFE_POOL_DIR, filename)
        if is_readable_image(path):
            valid.append(filename)
            seen.add(filename)
    return valid


def rebuild_local_wife_list() -> list[str]:
    """扫描本地老婆池并自动生成 list.txt。"""
    try:
        filenames = sorted(os.listdir(LOCAL_WIFE_POOL_DIR))
    except Exception:
        filenames = []
    valid = validate_local_wife_filenames(filenames)
    content = "\n".join(valid)
    try:
        with open(LOCAL_WIFE_LIST_FILE, "r", encoding="utf-8") as f:
            old_content = f.read()
    except Exception:
        old_content = None
    if old_content != content:
        with open(LOCAL_WIFE_LIST_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    return valid


def get_local_wife_pool_signature() -> str:
    """生成本地老婆池文件快照，用于发现 WebUI 上传/删除后的变化。"""
    entries = []
    try:
        filenames = sorted(os.listdir(LOCAL_WIFE_POOL_DIR))
    except Exception:
        filenames = []
    for filename in filenames:
        path = os.path.join(LOCAL_WIFE_POOL_DIR, filename)
        try:
            stat = os.stat(path)
        except Exception:
            continue
        if os.path.isfile(path):
            entries.append(f"{filename}\0{stat.st_size}\0{int(stat.st_mtime)}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def load_local_wife_list(use_uploaded_list: bool = False) -> list[str]:
    """读取本地老婆池列表；未上传 list.txt 时始终按当前目录自动生成。"""
    if not use_uploaded_list or not os.path.exists(LOCAL_WIFE_LIST_FILE):
        return rebuild_local_wife_list()
    try:
        with open(LOCAL_WIFE_LIST_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.read().splitlines() if line.strip()]
    except Exception:
        return rebuild_local_wife_list()
    valid = validate_local_wife_filenames(lines)
    if valid != lines:
        with open(LOCAL_WIFE_LIST_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(valid))
    return valid


def reset_today_wife_data(reason: str) -> None:
    """清理今天已抽结果和今日次数记录。"""
    today = get_today()
    changed_group_configs = 0
    removed_wives = 0

    for path in iter_group_config_files():
        cfg = load_json(path)
        if not isinstance(cfg, dict):
            continue
        new_cfg = {}
        for uid, wife_data in cfg.items():
            if isinstance(wife_data, list) and len(wife_data) > 1 and wife_data[1] == today:
                removed_wives += 1
                continue
            new_cfg[uid] = wife_data
        if new_cfg != cfg:
            save_json(path, new_cfg)
            changed_group_configs += 1

    removed_records = 0
    for record_type in ("change", "reset", "swap"):
        for gid, group_records in list(records.get(record_type, {}).items()):
            if not isinstance(group_records, dict):
                continue
            for uid, rec in list(group_records.items()):
                if isinstance(rec, dict) and rec.get("date") == today:
                    del group_records[uid]
                    removed_records += 1
            if not group_records:
                del records[record_type][gid]
    save_records()

    removed_swaps = 0
    for gid, group_requests in list(swap_requests.items()):
        if not isinstance(group_requests, dict):
            continue
        for uid, rec in list(group_requests.items()):
            if isinstance(rec, dict) and rec.get("date") == today:
                del group_requests[uid]
                removed_swaps += 1
        if not group_requests:
            del swap_requests[gid]
    save_swap_requests()

    logger.info(
        "%s，已重置今日老婆数据：群配置 %s 个，抽取结果 %s 条，次数记录 %s 条，交换请求 %s 条。",
        reason,
        changed_group_configs,
        removed_wives,
        removed_records,
        removed_swaps,
    )


# ==================== 数据加载和保存函数 ====================

def load_records():
    """加载所有记录数据"""
    raw = load_json(RECORDS_FILE)
    records.clear()
    records.update({
        "change": raw.get("change", {}),
        "reset": raw.get("reset", {}),
        "swap": raw.get("swap", {})
    })


def save_records():
    """保存所有记录数据"""
    save_json(RECORDS_FILE, records)


def load_swap_requests():
    """加载交换请求并清理过期数据"""
    raw = load_json(SWAP_REQUESTS_FILE)
    today = get_today()
    cleaned = {}
    
    for gid, reqs in raw.items():
        valid = {uid: rec for uid, rec in reqs.items() if rec.get("date") == today}
        if valid:
            cleaned[gid] = valid
    
    swap_requests.clear()
    swap_requests.update(cleaned)
    if raw != cleaned:
        save_json(SWAP_REQUESTS_FILE, cleaned)


def save_swap_requests():
    """保存交换请求"""
    save_json(SWAP_REQUESTS_FILE, swap_requests)


# 初始加载所有数据
load_records()
load_swap_requests()

# ==================== 主插件类 ====================


class WifePlugin(Star):
    """二次元老婆插件主类"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._init_config()
        self._init_commands()
        self.admins = self.load_admins()

    def _init_config(self):
        """初始化配置参数"""
        self.need_prefix = self.config.get("need_prefix")
        self.change_max_per_day = self.config.get("change_max_per_day")
        self.swap_max_per_day = self.config.get("swap_max_per_day")
        self.reset_max_uses_per_day = self.config.get("reset_max_uses_per_day")
        self.reset_success_rate = self.config.get("reset_success_rate")
        self.reset_mute_duration = self.config.get("reset_mute_duration")
        self.image_base_url = self.config.get("image_base_url").rstrip("/") + "/"
        self.image_list_url = self.config.get("image_list_url")
        self.wife_pool_source = self.config.get("wife_pool_source", LOCAL_WIFE_SOURCE_LOCAL_FIRST)
        self.wife_message_template = self.config.get("wife_message_template", DEFAULT_WIFE_MESSAGE_TEMPLATE)
        self.local_wife_pool_files = self.config.get("local_wife_pool_files", [])
        rebuild_local_wife_list()
        self._sync_runtime_state(force_reload_reset=True)

    def _refresh_runtime_config(self):
        """刷新 WebUI 保存后的配置值，避免必须重载插件才生效。"""
        self.image_base_url = self.config.get("image_base_url").rstrip("/") + "/"
        self.image_list_url = self.config.get("image_list_url")
        self.wife_pool_source = self.config.get("wife_pool_source", LOCAL_WIFE_SOURCE_LOCAL_FIRST)
        self.wife_message_template = self.config.get("wife_message_template", DEFAULT_WIFE_MESSAGE_TEMPLATE)
        self.local_wife_pool_files = self.config.get("local_wife_pool_files", [])
        rebuild_local_wife_list()
        self._sync_runtime_state()

    def _sync_runtime_state(self, force_reload_reset: bool = False):
        """检测热重载、老婆池来源和本地池文件变化，并重置今日数据。"""
        state = load_json(RUNTIME_STATE_FILE)
        old_source = state.get("wife_pool_source")
        old_signature = state.get("local_wife_pool_signature")
        new_signature = get_local_wife_pool_signature()
        reasons = []

        if force_reload_reset and state.get("initialized"):
            reasons.append("插件已热重载")
        if old_source and old_source != self.wife_pool_source:
            reasons.append("老婆池来源已切换")
        if old_signature and old_signature != new_signature:
            reasons.append("本地老婆池文件已变化")

        if reasons:
            reset_today_wife_data("；".join(reasons))

        if (
            old_source != self.wife_pool_source
            or old_signature != new_signature
            or not state.get("initialized")
        ):
            state["initialized"] = True
            state["wife_pool_source"] = self.wife_pool_source
            state["local_wife_pool_signature"] = new_signature
            state["updated_at"] = datetime.utcnow().isoformat()
            save_json(RUNTIME_STATE_FILE, state)

    def _has_uploaded_local_list(self) -> bool:
        """判断 WebUI 上传列表中是否仍包含 list.txt。"""
        files = self.local_wife_pool_files if isinstance(self.local_wife_pool_files, list) else []
        return any(os.path.basename(str(item)) == "list.txt" for item in files)

    def _init_commands(self):
        """初始化命令映射表"""
        self.commands = {
            "老婆帮助": self.wife_help,
            "抽老婆": self.animewife,
            "查老婆": self.search_wife,
            "换老婆": self.change_wife,
            "重置换": self.reset_change_wife,
            "交换老婆": self.swap_wife,
            "同意交换": self.agree_swap_wife,
            "拒绝交换": self.reject_swap_wife,
            "查看交换请求": self.view_swap_requests,
        }

    def load_admins(self) -> list:
        """加载管理员列表"""
        path = os.path.join("data", "cmd_config.json")
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
                admins = cfg.get("admins_id", [])
                return [str(admin_id) for admin_id in admins]
        except Exception:
            return []

    def parse_at_target(self, event: AstrMessageEvent) -> Optional[str]:
        """解析消息中的@目标用户"""
        if not event.message_obj or not hasattr(event.message_obj, "message"):
            return None
        for comp in event.message_obj.message:
            if isinstance(comp, At):
                return str(comp.qq)
        return None

    def parse_target(self, event: AstrMessageEvent) -> Optional[str]:
        """解析命令目标用户"""
        target = self.parse_at_target(event)
        if target:
            return target
        
        msg = event.message_str.strip()
        if msg.startswith("查老婆"):
            parts = msg.split(maxsplit=1)
            if len(parts) > 1:
                name = parts[1]
                group_id = str(event.message_obj.group_id)
                cfg = load_group_config(group_id)
                for uid, data in cfg.items():
                    if isinstance(data, list) and len(data) > 2:
                        if data[2] == name:
                            return uid
        return None

    # ==================== 消息处理 ====================

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_all_messages(self, event: AstrMessageEvent, *args, **kwargs):
        """消息分发处理（仅群聊监听）"""
        if not event.message_obj or not hasattr(event.message_obj, "group_id"):
            return
        
        # 检查是否需要前缀唤醒
        if self.need_prefix and not event.is_at_or_wake_command:
            return
        
        text = event.message_str.strip()
        for cmd, func in self.commands.items():
            if text.startswith(cmd):
                async for res in func(event):
                    yield res
                break

    # ==================== 抽老婆相关 ====================

    async def animewife(self, event: AstrMessageEvent):
        """抽老婆"""
        gid = str(event.message_obj.group_id)
        uid = str(event.get_sender_id())
        nick = event.get_sender_name()
        today = get_today()
        
        async with get_config_lock(gid):
            cfg = load_group_config(gid)
            wife_data = cfg.get(uid)
            
            if not wife_data or not isinstance(wife_data, list) or wife_data[1] != today:
                # 今天还没抽，清理过期的交换请求
                load_swap_requests()
                # 获取新老婆
                img = await self._fetch_wife_image()
                if not img:
                    yield event.plain_result("抱歉，今天的老婆获取失败了，请稍后再试~")
                    return
                cfg[uid] = [img, today, nick]
                save_group_config(gid, cfg)
            else:
                img = wife_data[0]
        
        # 生成并发送消息
        yield event.chain_result(self._build_wife_message(img, nick))

    async def _fetch_wife_image(self) -> Optional[str]:
        """获取老婆图片"""
        self._refresh_runtime_config()
        if self.wife_pool_source == LOCAL_WIFE_SOURCE_LOCAL_FIRST:
            local_img = self._fetch_local_wife_image()
            if local_img:
                return local_img
        return await self._fetch_online_wife_image()

    def _fetch_local_wife_image(self) -> Optional[str]:
        """从独立本地老婆池获取图片。"""
        local_imgs = load_local_wife_list(self._has_uploaded_local_list())
        if local_imgs:
            return LOCAL_WIFE_POOL_PREFIX + random.choice(local_imgs)
        return None

    async def _fetch_online_wife_image(self) -> Optional[str]:
        """使用原始插件逻辑获取老婆图片。"""
        # 优先使用本地图片
        try:
            local_imgs = os.listdir(IMG_DIR)
            if local_imgs:
                return random.choice(local_imgs)
        except Exception:
            pass
        
        # 读取本地缓存
        cached_lines = []
        cache_expired = True
        if os.path.exists(WIFE_LIST_CACHE_FILE):
            try:
                cache_expired = (time.time() - os.path.getmtime(WIFE_LIST_CACHE_FILE)) >= 3600
                with open(WIFE_LIST_CACHE_FILE, "r", encoding="utf-8") as f:
                    cached_lines = [line for line in f.read().splitlines() if line.strip()]
            except Exception:
                pass
        
        # 缓存有效，直接使用
        if not cache_expired and cached_lines:
            return random.choice(cached_lines)
        
        # 缓存过期或不存在，从网络获取
        try:
            url = self.image_list_url or self.image_base_url
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        lines = [line for line in text.splitlines() if line.strip()]
                        if lines:
                            with open(WIFE_LIST_CACHE_FILE, "w", encoding="utf-8") as f:
                                f.write("\n".join(lines))
                            return random.choice(lines)
        except Exception:
            pass
        
        # 网络失败时用过期缓存兜底
        if cached_lines:
            return random.choice(cached_lines)
        
        return None

    def _resolve_wife_image(self, img: str) -> Optional[str]:
        """解析图片对应的本地路径；返回 None 表示应按在线 URL 发送。"""
        if img.startswith(LOCAL_WIFE_POOL_PREFIX):
            path = os.path.join(LOCAL_WIFE_POOL_DIR, img[len(LOCAL_WIFE_POOL_PREFIX):])
            return path if os.path.exists(path) else None
        path = os.path.join(IMG_DIR, img)
        return path if os.path.exists(path) else None

    def _build_wife_image_component(self, img: str):
        """复用原图片发送逻辑，同时避免本地池缺失图片被拼成在线 URL。"""
        path = self._resolve_wife_image(img)
        if path:
            return Image.fromFileSystem(path)
        if img.startswith(LOCAL_WIFE_POOL_PREFIX):
            return None
        return Image.fromURL(self.image_base_url + img)

    def _build_wife_message(self, img: str, nick: str):
        """构建老婆消息链"""
        name = os.path.splitext(img)[0].split("/")[-1]
        
        if "!" in name:
            source, chara = name.split("!", 1)
        else:
            source, chara = "未知来源", name

        template = self.wife_message_template or DEFAULT_WIFE_MESSAGE_TEMPLATE
        text = (
            template.replace("<user_name>", nick)
            .replace("<place>", source)
            .replace("<wife_name>", chara)
        )
        
        try:
            image = self._build_wife_image_component(img)
            chain = [Plain(text)]
            if image:
                chain.append(image)
            return chain
        except Exception:
            return [Plain(text)]

    # ==================== 帮助命令 ====================

    async def wife_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """
【基础命令】
• 抽老婆 - 每天抽取一个二次元老婆
• 查老婆 [@用户] - 查看别人的老婆

【换老婆功能】
• 换老婆 - 丢弃当前老婆换新的
• 重置换 [@用户] - 重置换老婆的次数(失败会禁言)

【交换功能】
• 交换老婆 [@用户] - 向别人发起老婆交换请求
• 同意交换 [@发起者] - 同意交换请求
• 拒绝交换 [@发起者] - 拒绝交换请求
• 查看交换请求 - 查看当前的交换请求

💡 提示：部分命令有每日使用次数限制
"""
        yield event.plain_result(help_text.strip())

    async def search_wife(self, event: AstrMessageEvent):
        """查老婆"""
        gid = str(event.message_obj.group_id)
        tid = self.parse_target(event) or str(event.get_sender_id())
        today = get_today()
        
        cfg = load_group_config(gid)
        wife_data = cfg.get(tid)
        
        if not wife_data or not isinstance(wife_data, list) or wife_data[1] != today:
            yield event.plain_result("没有发现老婆的踪迹，快去抽一个试试吧~")
            return
        
        img, _, owner = wife_data
        
        name = os.path.splitext(img)[0].split("/")[-1]
        
        if "!" in name:
            source, chara = name.split("!", 1)
            text = f"{owner}的老婆是来自《{source}》的{chara}，羡慕吗？"
        else:
            text = f"{owner}的老婆是{name}，羡慕吗？"
        
        try:
            image = self._build_wife_image_component(img)
            chain = [Plain(text)]
            if image:
                chain.append(image)
            yield event.chain_result(chain)
        except Exception:
            yield event.plain_result(text)

    # ==================== 换老婆相关 ====================

    async def change_wife(self, event: AstrMessageEvent):
        """换老婆"""
        gid = str(event.message_obj.group_id)
        uid = str(event.get_sender_id())
        nick = event.get_sender_name()
        today = get_today()
        
        # 检查每日换老婆次数
        recs = records["change"].setdefault(gid, {})
        rec = recs.get(uid, {"date": "", "count": 0})
        
        if rec["date"] == today and rec["count"] >= self.change_max_per_day:
            yield event.plain_result(f"{nick}，你今天已经换了{self.change_max_per_day}次老婆啦，明天再来吧~")
            return
        
        # 检查是否有老婆并删除
        async with get_config_lock(gid):
            cfg = load_group_config(gid)
            if uid not in cfg or cfg[uid][1] != today:
                yield event.plain_result(f"{nick}，你今天还没有老婆，先去抽一个再来换吧~")
                return
            
            # 删除老婆
            del cfg[uid]
            save_group_config(gid, cfg)
        
        # 更新记录
        if rec["date"] != today:
            rec = {"date": today, "count": 1}
        else:
            rec["count"] += 1
        recs[uid] = rec
        save_records()
        
        # 取消相关交换请求
        cancel_msg = self.cancel_swap_on_wife_change(gid, [uid])
        if cancel_msg:
            yield event.plain_result(cancel_msg)
        
        # 立即展示新老婆
        async for res in self.animewife(event):
            yield res

    # ==================== 重置相关 ====================

    async def reset_change_wife(self, event: AstrMessageEvent):
        """重置换老婆次数"""
        gid = str(event.message_obj.group_id)
        uid = str(event.get_sender_id())
        nick = event.get_sender_name()
        today = get_today()
        
        # 管理员可直接重置他人
        if uid in self.admins:
            tid = self.parse_at_target(event) or uid
            grp = records["change"].setdefault(gid, {})
            if tid in grp:
                del grp[tid]
                save_records()
            yield event.chain_result([
                Plain("管理员操作：已重置"), At(qq=int(tid)), Plain("的换老婆次数。")
            ])
            return
        
        # 普通用户使用重置机会
        grp = records["reset"].setdefault(gid, {})
        rec = grp.get(uid, {"date": today, "count": 0})
        
        if rec.get("date") != today:
            rec = {"date": today, "count": 0}
        
        if rec["count"] >= self.reset_max_uses_per_day:
            yield event.plain_result(f"{nick}，你今天已经用完{self.reset_max_uses_per_day}次重置机会啦，明天再来吧~")
            return
        
        rec["count"] += 1
        grp[uid] = rec
        save_records()
        
        tid = self.parse_at_target(event) or uid
        
        if random.random() < self.reset_success_rate:
            grp2 = records["change"].setdefault(gid, {})
            if tid in grp2:
                del grp2[tid]
                save_records()
            yield event.chain_result([
                Plain("已重置"), At(qq=int(tid)), Plain("的换老婆次数。")
            ])
        else:
            try:
                await event.bot.set_group_ban(group_id=int(gid), user_id=int(uid), duration=self.reset_mute_duration)
            except Exception:
                pass
            yield event.plain_result(f"{nick}，重置换失败，被禁言{self.reset_mute_duration}秒，下次记得再接再厉哦~")

    # ==================== 交换老婆相关 ====================

    async def swap_wife(self, event: AstrMessageEvent):
        """发起交换老婆请求"""
        gid = str(event.message_obj.group_id)
        uid = str(event.get_sender_id())
        tid = self.parse_at_target(event)
        nick = event.get_sender_name()
        today = get_today()
        
        # 检查每日交换请求次数
        grp_limit = records["swap"].setdefault(gid, {})
        rec_lim = grp_limit.get(uid, {"date": "", "count": 0})
        
        if rec_lim["date"] != today:
            rec_lim = {"date": today, "count": 0}
        
        if rec_lim["count"] >= self.swap_max_per_day:
            yield event.plain_result(f"{nick}，你今天已经发起了{self.swap_max_per_day}次交换请求啦，明天再来吧~")
            return
        
        if not tid or tid == uid:
            yield event.plain_result(f"{nick}，请在命令后@你想交换的对象哦~")
            return
        
        # 检查双方是否都有老婆
        cfg = load_group_config(gid)
        for x in (uid, tid):
            if x not in cfg or cfg[x][1] != today:
                who = nick if x == uid else "对方"
                yield event.plain_result(f"{who}，今天还没有老婆，无法进行交换哦~")
                return
        
        # 记录交换请求
        rec_lim["count"] += 1
        grp_limit[uid] = rec_lim
        save_records()
        
        grp = swap_requests.setdefault(gid, {})
        grp[uid] = {"target": tid, "date": today}
        save_swap_requests()
        
        yield event.chain_result([
            Plain(f"{nick} 想和 "), At(qq=int(tid)),
            Plain(" 交换老婆啦！请对方用\"同意交换 @发起者\"或\"拒绝交换 @发起者\"来回应~")
        ])

    async def agree_swap_wife(self, event: AstrMessageEvent):
        """同意交换老婆"""
        gid = str(event.message_obj.group_id)
        tid = str(event.get_sender_id())
        uid = self.parse_at_target(event)
        nick = event.get_sender_name()
        
        grp = swap_requests.get(gid, {})
        rec = grp.get(uid)
        
        if not rec or rec.get("target") != tid:
            yield event.plain_result(f"{nick}，请在命令后@发起者，或用\"查看交换请求\"命令查看当前请求哦~")
            return
        
        # 删除请求
        del grp[uid]
        
        # 执行交换
        async with get_config_lock(gid):
            cfg = load_group_config(gid)
            cfg[uid][0], cfg[tid][0] = cfg[tid][0], cfg[uid][0]
            save_group_config(gid, cfg)
        
        # 保存交换请求删除
        save_swap_requests()
        
        # 取消相关交换请求
        cancel_msg = self.cancel_swap_on_wife_change(gid, [uid, tid])
        
        yield event.plain_result("交换成功！你们的老婆已经互换啦，祝幸福~")
        if cancel_msg:
            yield event.plain_result(cancel_msg)

    async def reject_swap_wife(self, event: AstrMessageEvent):
        """拒绝交换老婆"""
        gid = str(event.message_obj.group_id)
        tid = str(event.get_sender_id())
        uid = self.parse_at_target(event)
        nick = event.get_sender_name()
        
        grp = swap_requests.get(gid, {})
        rec = grp.get(uid)
        
        if not rec or rec.get("target") != tid:
            yield event.plain_result(f"{nick}，请在命令后@发起者，或用\"查看交换请求\"命令查看当前请求哦~")
            return
        
        del grp[uid]
        save_swap_requests()
        
        yield event.chain_result([
            At(qq=int(uid)), Plain("，对方婉拒了你的交换请求，下次加油吧~")
        ])

    async def view_swap_requests(self, event: AstrMessageEvent):
        """查看当前交换请求"""
        gid = str(event.message_obj.group_id)
        me = str(event.get_sender_id())
        
        grp = swap_requests.get(gid, {})
        cfg = load_group_config(gid)
        
        # 获取发起的和收到的请求
        my_req = grp.get(me)
        sent_targets = [my_req["target"]] if my_req else []
        received_from = [uid for uid, rec in grp.items() if rec.get("target") == me]
        
        if not sent_targets and not received_from:
            yield event.plain_result("你当前没有任何交换请求哦~")
            return
        
        parts = []
        for tid in sent_targets:
            name = cfg.get(tid, [None, None, "未知用户"])[2]
            parts.append(f"→ 你发起给 {name} 的交换请求")
        
        for uid in received_from:
            name = cfg.get(uid, [None, None, "未知用户"])[2]
            parts.append(f"→ {name} 发起给你的交换请求")
        
        text = "当前交换请求如下：\n" + "\n".join(parts) + "\n请在\"同意交换\"或\"拒绝交换\"命令后@发起者进行操作~"
        yield event.plain_result(text)

    # ==================== 辅助方法 ====================

    def cancel_swap_on_wife_change(self, gid: str, user_ids: list) -> Optional[str]:
        """检查并取消与指定用户相关的交换请求"""
        today = get_today()
        grp = swap_requests.get(gid, {})
        grp_limit = records["swap"].setdefault(gid, {})
        
        # 找出需要取消的交换请求
        to_cancel = [
            req_uid for req_uid, req in grp.items()
            if req_uid in user_ids or req.get("target") in user_ids
        ]
        
        if not to_cancel:
            return None
        
        # 取消请求并返还次数
        for req_uid in to_cancel:
            rec_lim = grp_limit.get(req_uid, {"date": "", "count": 0})
            if rec_lim.get("date") == today and rec_lim.get("count", 0) > 0:
                rec_lim["count"] = max(0, rec_lim["count"] - 1)
                grp_limit[req_uid] = rec_lim
            del grp[req_uid]
        
        save_swap_requests()
        save_records()
        
        return f"已自动取消 {len(to_cancel)} 条相关的交换请求并返还次数~"

    async def terminate(self):
        """插件卸载时清理资源"""
        # 清理群组配置锁
        config_locks.clear()
        
        # 清理全局数据
        records.clear()
        swap_requests.clear()
