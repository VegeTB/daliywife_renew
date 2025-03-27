from astrbot.api.all import *
import astrbot.api.event.filter as filter
from datetime import datetime, timedelta
import random
import json
import aiohttp
import asyncio
import logging
import traceback
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, List, Optional, Set, Tuple
from astrbot.api.event.filter import command, event_message_type, EventMessageType
import json
import datetime
import logging
import random
import hashlib
from typing import Dict, Any
logger = logging.getLogger("CheckInPlugin")

# --------------- 路径配置 ---------------
PLUGIN_DIR = Path(__file__).parent
PAIR_DATA_PATH = PLUGIN_DIR / "pair_data.json"
COOLING_DATA_PATH = PLUGIN_DIR / "cooling_data.json"
BLOCKED_USERS_PATH = PLUGIN_DIR / "blocked_users.json"
OPERATION_COUNTER_PATH = PLUGIN_DIR / "operation_counter.json"

# 数据存储路径（check）
DATA_DIR = os.path.join("data", "plugins", "astrbot_checkin_plugin")
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "checkin_data.json")


# --------------- 日志配置 ---------------
logger = logging.getLogger("DailyWife")

# --------------- 数据结构 ---------------
class GroupMember:
    """群成员数据类"""
    def __init__(self, data: dict):
        self.user_id: str = str(data["user_id"])
        self.nickname: str = data["nickname"]
        self.card: str = data["card"]
        
    @property
    def display_info(self) -> str:
        """带QQ号的显示信息"""
        return f"{self.card or self.nickname}({self.user_id})"

# --------------- 插件主类 ---------------
@register("DailyWife", "jmt059", "每日老婆插件", "v0.3beta", "https://github.com/jmt059/DailyWife")
class DailyWifePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.pair_data = self._load_pair_data()
        self.cooling_data = self._load_cooling_data()
        self.blocked_users = self._load_blocked_users()
        self._init_napcat_config()
        self._migrate_old_data()
        self._clean_invalid_cooling_records()
        self.config["default_cooling_hours"] = 6
        self.operation_counter = self._load_operation_counter()  # 结构改为 {群ID: {日期: {用户ID: 次数}}}

        # --------------- 数据迁移 ---------------
    def _migrate_old_data(self):
        """数据格式迁移"""
        try:
            # 迁移旧版屏蔽数据（v3.0.x -> v3.1.x）
            if "block_list" in self.config:
                self.blocked_users = set(map(str, self.config["block_list"]))
                self._save_blocked_users()
                del self.config["block_list"]
            
            # 迁移配对数据格式（v2.x -> v3.x）
            for group_id in list(self.pair_data.keys()):
                pairs = self.pair_data[group_id].get("pairs", {})
            for uid in pairs:
                if "is_initiator" not in pairs[uid]:
                    pairs[uid]["is_initiator"] = (uid == user_id)  # 旧数据默认发起者为抽方
                if isinstance(pairs, dict) and all(isinstance(v, str) for v in pairs.values()):
                    new_pairs = {}
                    for user_id, target_id in pairs.items():
                        new_pairs[user_id] = {
                            "user_id": target_id,
                            "display_name": f"未知用户({target_id})"
                        }
                        if target_id in pairs:
                            new_pairs[target_id] = {
                                "user_id": user_id,
                                "display_name": f"未知用户({user_id})"
                            }
                    self.pair_data[group_id]["pairs"] = new_pairs
                    self._save_pair_data()
        except Exception as e:
            logger.error(f"数据迁移失败: {traceback.format_exc()}")

    # --------------- 初始化方法 ---------------
    def _init_napcat_config(self):
        """初始化Napcat连接配置"""
        try:
            self.napcat_host = self.config.get("napcat_host") or "127.0.0.1:3000"
            parsed = urlparse(f"http://{self.napcat_host}")
            if not parsed.hostname or not parsed.port:
                raise ValueError("无效的Napcat地址格式")
            self.napcat_hostname = parsed.hostname
            self.napcat_port = parsed.port
            self.timeout = self.config.get("request_timeout") or 10
        except Exception as e:
            logger.error(f"Napcat配置错误: {traceback.format_exc()}")
            raise RuntimeError("Napcat配置初始化失败")

    # --------------- 数据管理 ---------------
    def _load_pair_data(self) -> Dict:
        """加载配对数据"""
        try:
            if PAIR_DATA_PATH.exists():
                with open(PAIR_DATA_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"配对数据加载失败: {traceback.format_exc()}")
            return {}

    def _load_operation_counter(self) -> Dict:
        """加载操作计数器"""
        try:
            if OPERATION_COUNTER_PATH.exists():
                with open(OPERATION_COUNTER_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"操作计数器加载失败: {traceback.format_exc()}")
            return {}

    def _save_operation_counter(self):
        """保存操作计数器"""
        self._save_data(OPERATION_COUNTER_PATH, self.operation_counter)

    def _load_cooling_data(self) -> Dict:
        """加载冷静期数据"""
        try:
            if COOLING_DATA_PATH.exists():
                with open(COOLING_DATA_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return {
                        k: {
                            "users": v["users"],
                            "expire_time": datetime.fromisoformat(v["expire_time"])
                        } for k, v in data.items()
                    }
            return {}
        except Exception as e:
            logger.error(f"冷静期数据加载失败: {traceback.format_exc()}")
            return {}

    # def _load_operation_counter(self) -> Dict:
    #     """加载操作计数器（支持多命令类型）"""
    #     try:
    #         if OPERATION_COUNTER_PATH.exists():
    #             with open(OPERATION_COUNTER_PATH, "r", encoding="utf-8") as f:
    #                 raw_data = json.load(f)
    #                 return self._migrate_counter_data(raw_data)
    #         return {}
    #     except Exception as e:
    #         logger.error(f"操作计数器加载失败: {traceback.format_exc()}")
    #         return {}

    def _load_blocked_users(self) -> Set[str]:
        """加载屏蔽用户列表"""
        try:
            if BLOCKED_USERS_PATH.exists():
                with open(BLOCKED_USERS_PATH, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            return set()
        except Exception as e:
            logger.error(f"屏蔽列表加载失败: {traceback.format_exc()}")
            return set()

    def _save_pair_data(self):
        """安全保存配对数据"""
        self._save_data(PAIR_DATA_PATH, self.pair_data)

    def _save_cooling_data(self):
        """安全保存冷静期数据"""
        temp_data = {
            k: {
                "users": v["users"],
                "expire_time": v["expire_time"].isoformat()
            } for k, v in self.cooling_data.items()
        }
        self._save_data(COOLING_DATA_PATH, temp_data)

    def _save_blocked_users(self):
        """保存屏蔽用户列表"""
        self._save_data(BLOCKED_USERS_PATH, list(self.blocked_users))

    def _save_data(self, path: Path, data: dict):
        """通用保存方法"""
        try:
            temp_path = path.with_suffix(".tmp")
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            temp_path.replace(path)
        except Exception as e:
            logger.error(f"数据保存失败: {traceback.format_exc()}")

        # --------------- 新增限制检查逻辑 ---------------

    async def _record_operation(self, group_id: str, user_id: str, cmd_type: str):
        """通用次数记录"""
        today = datetime.now().strftime("%Y-%m-%d")
        group_id = str(group_id)
        user_id = str(user_id)

    async def _check_c01_limit(self, group_id: str, user_id: str, event: AstrMessageEvent) -> Optional[str]:
        """检查C01操作限制（新增群组维度）"""
        today = datetime.now().strftime("%Y-%m-%d")
        group_id = str(event.get_group_id())
        user_id = str(user_id)

        # 获取当日操作次数
        daily_count = self.operation_counter.get(group_id, {}).get(today, {}).get(user_id, 0)

        if daily_count >= 2:
            yield event.plain_result(f"⚠️ 今日配对次数已达上限（2次），请明日再试")
            return

    async def _record_c01_operation(self, group_id: str, user_id: str):
        """记录C01操作（新增群组维度）"""
        today = datetime.now().strftime("%Y-%m-%d")
        group_id = str(group_id)
        user_id = str(user_id)

        # 初始化数据结构
        if group_id not in self.operation_counter:
            self.operation_counter[group_id] = {}
        if today not in self.operation_counter[group_id]:
            self.operation_counter[group_id][today] = {}

        # 递增计数
        current = self.operation_counter[group_id][today].get(user_id, 0)
        self.operation_counter[group_id][today][user_id] = current + 1
        self._save_operation_counter()


    # async def _record_operation(self, group_id: str, user_id: str, cmd_type: str):
    #     """通用次数记录"""
    #     today = datetime.now().strftime("%Y-%m-%d")
    #     group_id = str(group_id)
    #     user_id = str(user_id)
    #
    #     # 初始化数据结构
    #     if group_id not in self.operation_counter:
    #         self.operation_counter[group_id] = {}
    #     if today not in self.operation_counter[group_id]:
    #         self.operation_counter[group_id][today] = {}
    #     self.operation_counter.setdefault(group_id, {}).setdefault(today, {}).setdefault(user_id,
    #                                                                                      {"C01": 0, "revoke": 0})
    #
    #     # 递增计数
    #     self.operation_counter[group_id][today][user_id][cmd_type] += 1
    #     self._save_operation_counter()
    #
    # --------------- 管理员验证 ---------------
    def _is_admin(self, user_id: str) -> bool:
        """验证管理员权限"""
        admin_list = ["969105299"]
        return str(user_id) in map(str, admin_list)

    # --------------- 命令处理器 ---------------
    @filter.command("重置")
    async def reset_command_handler(self, event: AstrMessageEvent):
        """完整的重置命令处理器"""
        if not self._is_admin(event.get_sender_id()):
            yield event.plain_result("⚠ 权限不足，需要管理员权限")
            return

        args = event.message_str.split()[1:]
        if not args:
            yield event.plain_result("❌ 参数错误\n格式：重置 [群号/-a/-c]")
            return

        arg = args[0]
        if arg == "-a":
            self.pair_data = {}
            self._save_pair_data()
            yield event.plain_result("✅ 已重置所有群组的配对数据")
        elif arg == "-c":
            self.cooling_data = {}
            self._save_cooling_data()
            yield event.plain_result("✅ 已重置所有冷静期数据")
        elif arg.isdigit():
            group_id = str(arg)
            if group_id in self.pair_data:
                del self.pair_data[group_id]
                self._save_pair_data()
                yield event.plain_result(f"✅ 已重置群组 {group_id} 的配对数据")
            else:
                yield event.plain_result(f"⚠ 未找到群组 {group_id} 的记录")
        else:
            yield event.plain_result("❌ 无效参数\n可用参数：群号/-a(全部)/-c(冷静期)")

    async def _check_operation_limit(self, user_id: str) -> Optional[str]:
        """检查操作限制"""
        today = datetime.now().strftime("%Y-%m-%d")
        user_id = str(user_id)

        # 获取当日操作次数
        daily_ops = self.operation_counter.get(today, {}).get(user_id, 0)

        if daily_ops >= 2:
            return "⚠️ 今日配对/撤销次数已达上限，请明日再试"
        return None

    async def _record_operation(self, user_id: str):
        """记录操作次数"""
        today = datetime.now().strftime("%Y-%m-%d")
        user_id = str(user_id)

        if today not in self.operation_counter:
            self.operation_counter[today] = {}

        current = self.operation_counter[today].get(user_id, 0)
        self.operation_counter[today][user_id] = current + 1
        self._save_operation_counter()

    @filter.command("重置次数")
    async def reset_counter(self, event: AstrMessageEvent):
        if not self._is_admin(event.get_sender_id()):
            yield event.plain_result("权限不足。")
            return

        ats = []
        chain = event.message_obj.message
        args = event.message_str.split()
        # if len(args) < 2 or not args[1].isdigit():
        #     yield event.plain_result("格式：/重置次数 [QQ号]")
        #     return
        for comp in chain:
            if isinstance(comp, At):
                qq = str(comp.qq)
                ats.append(qq)
        if not ats:
            yield event.plain_result("请在指令后 @ 一个用户。")
            return

        target_id = str(ats[0])
        today = datetime.now().strftime("%Y-%m-%d")
        group_id = str(event.message_obj.group_id)

        if group_id in self.operation_counter and today in self.operation_counter[group_id]:
            if target_id in self.operation_counter[group_id][today]:
                del self.operation_counter[group_id][today][target_id]
                self._save_operation_counter()

        yield event.plain_result(f"已重置公民 {target_id} 的当日申请次数")

    @filter.command("屏蔽")
    async def block_command_handler(self, event: AstrMessageEvent):
        """完整的屏蔽命令处理器"""
        if not self._is_admin(event.get_sender_id()):
            yield event.plain_result("⚠ 权限不足，需要管理员权限")
            return

        qq = event.message_str.split()[1] if len(event.message_str.split()) > 1 else None
        if not qq or not qq.isdigit():
            yield event.plain_result("❌ 参数错误\n格式：屏蔽 [QQ号]")
            return

        qq_str = str(qq)
        if qq_str in self.blocked_users:
            yield event.plain_result(f"ℹ️ 用户 {qq} 已在屏蔽列表中")
        else:
            self.blocked_users.add(qq_str)
            self._save_blocked_users()
            yield event.plain_result(f"✅ 已屏蔽用户 {qq}")

    @filter.command("冷静期")
    async def cooling_command_handler(self, event: AstrMessageEvent):
        """完整的冷静期命令处理器"""
        if not self._is_admin(event.get_sender_id()):
            yield event.plain_result("⚠ 权限不足，需要管理员权限")
            return

        args = event.message_str.split()
        if len(args) < 2 or not args[1].isdigit():
            yield event.plain_result("❌ 参数错误\n格式：冷静期 [小时数]")
            return

        hours = int(args[1])
        if not 1 <= hours <= 720:
            yield event.plain_result("❌ 无效时长（1-720小时）")
            return

        self.config["default_cooling_hours"] = hours
        yield event.plain_result(f"✅ 已设置默认冷静期时间为 {hours} 小时")

    # --------------- 核心功能 ---------------
    async def _get_members(self, group_id: int) -> Optional[List[GroupMember]]:
        """获取有效群成员"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://{self.napcat_host}/get_group_member_list",
                    json={"group_id": group_id},
                    timeout=self.timeout
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"HTTP状态码异常: {resp.status}")
                        return None
                    
                    data = await resp.json()
                    if data["status"] != "ok":
                        logger.error(f"API返回状态异常: {data}")
                        return None
                    
                    return [
                        GroupMember(m) for m in data["data"]
                        if str(m["user_id"]) not in self.blocked_users
                    ]
        except Exception as e:
            logger.error(f"获取群成员失败: {traceback.format_exc()}")
            return None

    def _check_reset(self, group_id: str):
        """每日重置检查"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            # 清理过期的操作记录（保留3天）
            cutoff_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
            for date_str in list(self.operation_counter.keys()):
                if date_str < cutoff_date:
                    del self.operation_counter[date_str]
            if group_id not in self.pair_data or self.pair_data[group_id].get("date") != today:
                self.pair_data[group_id] = {
                    "date": today,
                    "pairs": {},
                    "used": []
                }
                self._save_pair_data()
        except Exception as e:
            logger.error(f"重置检查失败: {traceback.format_exc()}")

    # --------------- 用户功能 ---------------
    @filter.command("C01", alias = ["c01", "C-01", "c-01"])
    async def pair_handler(self, event: AstrMessageEvent):
        """配对功能"""


        try:
            if not hasattr(event.message_obj, "group_id"):
                return

            group_id = str(event.message_obj.group_id)
            user_id = event.get_sender_id()
            bot_id = event.message_obj.self_id

            # limit_msg = await self._check_c01_limit(group_id, user_id)
            # if limit_msg:
            #     yield event.plain_result(limit_msg)
            #     return

            self._check_reset(group_id)
            group_data = self.pair_data[group_id]

            if user_id in group_data["pairs"]:
                # 获取角色信息
                is_initiator = group_data["pairs"][user_id].get("is_initiator", False)

                if is_initiator:
                    # 抽方专属回复
                    reply = [
                        Plain("🌏【C-01受理回执】\n"),
                        Plain(f"▸ 公民，你已成功与：{group_data['pairs'][user_id]['display_name']}结婚\n"),
                        Plain(f"▸ 该表格有效期至：今日2400"),
                    ]
                else:
                    # 被抽方专属回复
                    reply = [
                        Plain("🌏【C-01受理回执】\n"),
                        Plain(f"✦ 公民，你已成功与： {group_data['pairs'][user_id]['display_name']} 结婚\n"),
                        Plain(f"✦ 该表格有效期至：今日2400"),
                    ]

                
                yield event.chain_result(reply)
                return

            # 检查操作限制
            today = datetime.now().strftime("%Y-%m-%d")
            group_id = str(group_id)
            user_id = str(user_id)

            # 获取当日操作次数
            daily_count = self.operation_counter.get(group_id, {}).get(today, {}).get(user_id, 0)
            user_id = str(event.get_sender_id())
            group_id = str(event.get_group_id())
            today = datetime.now().strftime("%Y-%m-%d")
            # 初始化数据结构
            if group_id not in self.operation_counter:
                self.operation_counter[group_id] = {}
            if today not in self.operation_counter[group_id]:
                self.operation_counter[group_id][today] = {}
            # 递增计数
            current = self.operation_counter[group_id][today].get(user_id, 0)
            # yield event.plain_result(f"{current}")
            self.operation_counter[group_id][today][user_id] = current + 1
            self._save_operation_counter()
            self._save_data(OPERATION_COUNTER_PATH, self.operation_counter)

            # yield event.plain_result(str(daily_count))

            if daily_count >= 2:
                yield event.plain_result(f"🌏【C-01受理回执】\n⛔公民，你的申请因超出次数限制而被驳回。")
                return

            members = await self._get_members(int(group_id))
            if not members:
                yield event.plain_result("🌏服务暂不可用")
                return

            valid_members = [
                m for m in members
                if m.user_id not in {user_id, bot_id}
                and m.user_id not in group_data["used"]
                and not self._is_in_cooling_period(user_id, m.user_id)
            ]



            target = None
            for _ in range(5):
                if not valid_members:
                    break
                target = random.choice(valid_members)
                if target.user_id not in group_data["pairs"]:
                    break
                valid_members.remove(target)
                target = None
            
            if not target:
                yield event.plain_result("🌏【C-01受理回执】\n公民，你的申请未被批准。")
                return




            else:
                # C-01计数

                group_data["pairs"][user_id] = {
                    "user_id": target.user_id,
                    "display_name": target.display_info,
                    "is_initiator": True  # 标记抽方
                }
                group_data["pairs"][target.user_id] = {
                    "user_id": user_id,
                    "display_name": f"{event.get_sender_name()}({user_id})",
                    "is_initiator": False  # 标记被抽方
                }
                group_data["used"].extend([user_id, target.user_id])
                self._save_pair_data()

                avatar_url = f"http://q.qlogo.cn/headimg_dl?dst_uin={target.user_id}&spec=640"
                # 给抽方的提示（在未配对时首次发送命令的人） (is_initiator=True)
                yield event.chain_result([
                    Plain(f"🌏【C-01受理回执】\n"),
                    Plain(f"恭喜公民{event.get_sender_name()}({user_id})申请通过审批\n"),
                    Plain(f"▻ 为你分配结婚对象{target.display_info}\n"),
                    Plain(f"▻ 对方头像："),
                    Image.fromURL(avatar_url),
                    Plain(f"\n请民主地交往。"),
                    Plain(f"\n使用 查询C01 查看详细信息")
                ])


        except Exception as e:
            logger.error(f"配对失败: {traceback.format_exc()}")
            yield event.plain_result("❌ 分配系统异常")
        await self._daily_reset_task()

    # ================== 修复后的查询老婆命令 ==================
    @filter.command("查询C01", alias = ["查询c01"])
    async def query_handler(self, event: AstrMessageEvent):
        """查询伴侣"""

        try:
            group_id = str(event.message_obj.group_id)
            user_id = event.get_sender_id()
            
            self._check_reset(group_id)
            group_data = self.pair_data.get(group_id, {})

            # 先检查是否存在CP关系
            if user_id not in group_data.get("pairs", {}):
                yield event.plain_result("🌏 公民，你还未被分配对象。")
                return

            target_info = group_data["pairs"][user_id]
            avatar_url = f"http://q.qlogo.cn/headimg_dl?dst_uin={target_info['user_id']}&spec=640"

            # 角色判断逻辑
            if target_info.get("is_initiator", False):
                role_desc = "🌏 公民，你的今日对象"
                footer = "\n请民主地交往。"
            else:
                role_desc = "🌏 公民，你的今日对象"
                footer = "\n请民主地交往。"
                
            yield event.chain_result([
                Plain(f"{role_desc}：{target_info['display_name']}{footer}"),
                At(qq=target_info["user_id"]),
                Image.fromURL(avatar_url)
            ])

        except Exception as e:
            logger.error(f"查询失败: {traceback.format_exc()}")
            yield event.plain_result("❌ 查询过程发生异常")

    # ================== 修复后的分手命令 ==================
    @filter.command("撤销C01", alias=["撤销c01"])
    async def breakup_handler(self, event: AstrMessageEvent):
        """解除伴侣关系"""
        try:
            group_id = str(event.message_obj.group_id)
            user_id = event.get_sender_id()
            user_name = event.get_sender_name()

            if group_id not in self.pair_data or user_id not in self.pair_data[group_id]["pairs"]:
                yield event.plain_result("🌏 公民，你还未被分配对象。")
                return

            target_info = self.pair_data[group_id]["pairs"][user_id]
            target_id = target_info["user_id"]
            is_initiator = target_info.get("is_initiator", False)  # 先获取身份信息

            # 删除配对数据
            del self.pair_data[group_id]["pairs"][user_id]
            del self.pair_data[group_id]["pairs"][target_id]
            self.pair_data[group_id]["used"] = [uid for uid in self.pair_data[group_id]["used"] if
                                                uid not in {user_id, target_id}]
            self._save_pair_data()

            # 设置冷静期
            cooling_key = f"{user_id}-{target_id}"
            cooling_hours = self.config.get("default_cooling_hours", 48)
            self.cooling_data[cooling_key] = {
                "users": [user_id],
                "expire_time": datetime.now() + timedelta(hours=cooling_hours)
            }
            self._save_cooling_data()


            action = "⚠️由超级地球繁荣部批准撤销C-01授权！\n🔊繁荣部提示：每日每位公民至多可提交两份C-01表格。"
            yield event.chain_result([
                Plain(f"{action}")
            ])

        except Exception as e:
            logger.error(f"⛔撤销操作失败: {traceback.format_exc()}")
            yield event.plain_result("⛔ 撤销操作异常")

        # 配套的冷静期检查方法
        def _is_in_cooling_period(self, group_id: str, user1: str, user2: str) -> bool:
            """检查指定群组的冷静期状态"""
            now = datetime.now()
            sorted_users = sorted([user1, user2])
            cooling_key = f"{group_id}-{sorted_users[0]}-{sorted_users[1]}"

            record = self.cooling_data.get(cooling_key)
            if not record:
                return False

            # 同时验证群组匹配和有效期
            return (
                    record["group_id"] == group_id and
                    user1 in record["users"] and
                    user2 in record["users"] and
                    now < record["expire_time"]
            )

    # --------------- 辅助功能 ---------------
    def _clean_invalid_cooling_records(self):
        """每日清理过期的冷静期记录"""
        try:
            now = datetime.now()
            expired_keys = [
                k for k, v in self.cooling_data.items()
                if v["expire_time"] < now
            ]
            for k in expired_keys:
                del self.cooling_data[k]
            if expired_keys:
                self._save_cooling_data()
                logger.info(f"已清理 {len(expired_keys)} 条过期冷静期记录")
        except Exception as e:
            logger.error(f"清理冷静期数据失败: {traceback.format_exc()}")

    def _is_in_cooling_period(self, user1: str, user2: str) -> bool:
        """检查是否在冷静期"""
        cooling_hours = self.config.get("default_cooling_hours", 48)
        return any(
            {user1} in set(pair["users"]) and
            {user2} in set(pair["users"]) and
            datetime.now() < pair["expire_time"]
            for pair in self.cooling_data.values()
        )

    # --------------- 帮助信息 ---------------
    # @filter.command("老婆帮帮我")  # 改为更直观的中文命令
    # async def help_handler(self, event: AstrMessageEvent):
    #     """帮助信息"""
    #     help_msg = f"""
    #     【老婆插件使用说明】
    #     🌸 基础功能：
    #     /今日老婆 - 随机配对CP
    #     /查询老婆 - 查询当前CP
    #     /我要分手 - 解除当前CP关系
    #
    #     ⚙️ 管理员命令：
    #     /重置 [群号] - 重置指定群数据
    #     /重置 -a      - 重置所有群数据
    #     /重置 -c      - 重置冷静期数据
    #     /屏蔽 [QQ号]  - 屏蔽指定用户
    #     /冷静期 [小时] - 设置冷静期时长
    #
    #     📌 注意事项：
    #     1. 命令需以斜杠开头（如 /今日老婆）
    #     2. 解除关系后需间隔 {self.config.get('default_cooling_hours', 48)} 小时才能再次匹配
    #     """
    #     yield event.chain_result([Plain(help_msg.strip())])

    # --------------- 定时任务 ---------------
    async def _daily_reset_task(self):
        """每日定时任务（支持多命令类型）"""
        while True:
            now = datetime.now()
            next_day = now + timedelta(days=1)
            reset_time = datetime(next_day.year, next_day.month, next_day.day, 0, 0, 5)
            wait_seconds = (reset_time - now).total_seconds()

            await asyncio.sleep(wait_seconds)
            try:
                # 清理三天前的数据
                cutoff_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
                for group_id in list(self.operation_counter.keys()):
                    # 清理过期日期
                    valid_dates = [
                        d for d in self.operation_counter[group_id].keys()
                        if d >= cutoff_date
                    ]
                    # 清理空用户数据
                    for date_str in valid_dates:
                        users = self.operation_counter[group_id][date_str]
                        self.operation_counter[group_id][date_str] = {
                            uid: counts for uid, counts in users.items()
                            if sum(counts.values()) > 0
                        }
                    # 清理空群组
                    if not valid_dates:
                        del self.operation_counter[group_id]
                self._save_operation_counter()
                logger.info("每日操作计数器已清理")
            except Exception as e:
                logger.error(f"定时任务失败: {traceback.format_exc()}")

    def __del__(self):
        """析构时启动定时任务"""
        asyncio.create_task(self._daily_reset_task())








# ===========================================================================================
#                                      check插件转移
# ===========================================================================================


# 训练手册提示
MOTIVATIONAL_MESSAGES = [
    "别死！",
    "困惑之时，不要思考——只需大喊“为了民主！”，"
    "然后勇敢地冲锋陷阵。",
    "为！了！超！级！地！球！",
    "Say hello to DEMOCRACY！",
    "喝酒不开船。",
    "多多称赞队友的出色表现。我们都走在历史的康庄大道上！",
    "绝地喷射仓具备基础转向功能。尽量降落在敌人周围！",
    "机器人配备过度反应协议，因此，火力压制对付他们格外有效。",
    "管理式民主是先进文明的支柱。",
    "进行可能产生孩子的行为之前，记得先填好C-01授权表格。",
    "不要担心，就算你没能完成目标，你也绝对不会被送入自由营。那只是异见分子散播的谣言。",
    "对于试图对话的敌人，要毫不犹豫地开枪击毙。一定不能为花言巧语所欺骗。",
    "如果你发现队友同情敌人，请向民主官举报。思想犯罪是会害死人的！",
    "牢记自由！",
    "扣↓↑←↓↑→↓↑送地狱火。",
    "扣↓→↑↑↑送地狱火",
    "抽到这条tip的人发一张腿照。",
    "抽到这条tip的人发一张腿照。",
    "抽到这条tip的人发一张腿照。",
    "抽到这条tip的人抽到这条tip。",
    "点击输入文本。",
    "吃什么？",
    "我重启了。",
    "注意休息！…前提是你想被当作懦夫的话。",
    "🈶🈚民主？",
    "抽到这条tip的人今天记得服役。",
    "👊🔥🌪🔥 神龙裂破！",
    "👆😡👆 MUSCLE!",
    "每天进行脱敏训练，确保你能冷静面对敌人的暴行。"
]


def _load_data() -> dict:
    """加载签到数据"""
    try:
        if not os.path.exists(DATA_FILE):
            return {}
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"数据加载失败: {str(e)}")
        return {}


def _save_data(data: dict):
    """保存签到数据"""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"数据保存失败: {str(e)}")


def _get_context_id(event: AstrMessageEvent) -> str:
    """多平台兼容的上下文ID生成（已修复QQ官方Webhook问题）"""
    try:
        # 优先处理QQ官方Webhook结构
        if hasattr(event, 'message') and hasattr(event.message, 'source'):
            source = event.message.source
            if hasattr(source, 'group_id') and source.group_id:
                return f"group_{source.group_id}"
            if hasattr(source, 'user_id') and source.user_id:
                return f"private_{source.user_id}"

        # 处理标准事件结构
        if hasattr(event, 'group_id') and event.group_id:
            return f"group_{event.group_id}"
        if hasattr(event, 'user_id') and event.user_id:
            return f"private_{event.user_id}"

        # 生成唯一备用ID
        event_str = f"{event.get_message_id()}-{event.get_time()}"
        return f"ctx_{hashlib.md5(event_str.encode()).hexdigest()[:6]}"

    except Exception as e:
        logger.error(f"上下文ID生成异常: {str(e)}")
        return "default_ctx"


def _generate_rewards() -> int:
    """生成1-10随机战争债券奖章"""
    return random.randint(1, 10)


@register("签到插件", "Kimi&Meguminlove", "多维度排行榜签到系统", "1.0.3",
          "https://github.com/Meguminlove/astrbot_checkin_plugin")
class CheckInPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data = _load_data()

    @command("训练手册", alias=["手册"])
    async def meg(self, event: AstrMessageEvent):
        selected_msg = random.choice(MOTIVATIONAL_MESSAGES)
        yield event.plain_result(
            f"🔊 训练手册提示: {selected_msg}"
        )

    @command("解冻", alias=["打卡"])
    async def check_in(self, event: AstrMessageEvent):
        """每日签到"""
        try:
            ctx_id = _get_context_id(event)
            user_id = event.get_sender_id()
            today = datetime.date.today().isoformat()

            # 初始化数据结构（新增username字段）
            ctx_data = self.data.setdefault(ctx_id, {})
            user_data = ctx_data.setdefault(user_id, {
                "username": event.get_sender_name(),  # 确保存储的是用户昵称
                "total_days": 0,
                "continuous_days": 0,
                "month_days": 0,
                "total_rewards": 0,
                "month_rewards": 0,
                "last_checkin": None
            })

            # 更新用户名（防止用户改名）
            user_data['username'] = event.get_sender_name()

            # 检查重复签到
            if user_data["last_checkin"] == today:
                yield event.plain_result("终端拒绝受理。理由：重复的解冻请求。\n❕输入 /解冻排行 可以查看奖章排行榜。")
                return

            # 计算连续签到
            last_date = user_data["last_checkin"]
            current_month = today[:7]

            if last_date:
                last_day = datetime.date.fromisoformat(last_date)
                if (datetime.date.today() - last_day).days == 1:
                    user_data["continuous_days"] += 1
                else:
                    user_data["continuous_days"] = 1

                # 跨月重置月数据
                if last_date[:7] != current_month:
                    user_data["month_days"] = 0
                    user_data["month_rewards"] = 0
            else:
                user_data["continuous_days"] = 1

            # 生成奖励
            rewards = _generate_rewards()
            user_data.update({
                "total_days": user_data["total_days"] + 1,
                "month_days": user_data["month_days"] + 1,
                "total_rewards": user_data["total_rewards"] + rewards,
                "month_rewards": user_data["month_rewards"] + rewards,
                "last_checkin": today
            })

            _save_data(self.data)

            # 构造响应
            selected_msg = random.choice(MOTIVATIONAL_MESSAGES)
            name = event.get_sender_name()
            yield event.plain_result(
                f"✅【解冻成功】\n民主向你问好，{name}\n"
                f"🌎 终端提示: 你已坚持宣扬管理式民主{user_data['continuous_days']}天\n"
                f"🎖️ 获得战争债券奖章: {rewards}个\n"
                f"🔊 训练手册提示: {selected_msg}"
            )

        except Exception as e:
            logger.error(f"解冻异常: {str(e)}", exc_info=True)
            yield event.plain_result("🔧 解冻服务暂时不可用。")

    def _get_rank(self, event: AstrMessageEvent, key: str) -> list:
        """获取当前上下文的排行榜"""
        ctx_id = _get_context_id(event)
        ctx_data = self.data.get(ctx_id, {})
        return sorted(
            ctx_data.items(),
            key=lambda x: x[1][key],
            reverse=True
        )[:10]

    # @command("解冻排行榜", alias=["解冻排行"])
    # async def show_rank_menu(self, event: AstrMessageEvent):
    #     """排行榜导航菜单"""
    #     yield event.plain_result(
    #         "📊 排行榜类型：\n"
    #         "/解冻总奖励排行榜 - 累计获得超级货币\n"
    #         "/解冻月奖励排行榜 - 本月获得超级货币\n"
    #         "/解冻总天数排行榜 - 历史解冻总天数\n"
    #         "/解冻月天数排行榜 - 本月解冻天数\n"
    #         "/解冻今日排行榜 - 今日解冻潜兵榜"
    #     )

    # @command("解冻排行榜", alias=["解冻排行"])
    # async def total_rewards_rank(self, event: AstrMessageEvent):
    #     """总奖励排行榜"""
    #     ranked = self._get_rank(event, "total_rewards")
    #     msg = ["🌎 累计奖章排行榜"] + [
    #         f"{i+1}. 潜兵 {data.get('username', '未知')} - {data['total_rewards']}个"
    #         for i, (uid, data) in enumerate(ranked)
    #     ]
    #     yield event.plain_result("\n".join(msg))

    @command("解冻排行榜", alias=["解冻排行"])
    async def month_rewards_rank(self, event: AstrMessageEvent):
        """月奖励排行榜"""
        ranked = self._get_rank(event, "month_rewards")
        msg = ["🌎 本月奖章排行榜"] + [
            f"{i + 1}. 潜兵 {data.get('username', '未知')} - {data['month_rewards']}个"
            for i, (uid, data) in enumerate(ranked)
        ]
        yield event.plain_result("\n".join(msg))

    #
    # @command("签到总天数排行榜", alias=["签到总天数排行"])
    # async def total_days_rank(self, event: AstrMessageEvent):
    #     """总天数排行榜"""
    #     ranked = self._get_rank(event, "total_days")
    #     msg = ["🏆 累计契约天数榜"] + [
    #         f"{i+1}. 契约者 {data.get('username', '未知')} - {data['total_days']}天"
    #         for i, (uid, data) in enumerate(ranked)
    #     ]
    #     yield event.plain_result("\n".join(msg))
    #
    # @command("签到月天数排行榜", alias=["签到月天数排行"])
    # async def month_days_rank(self, event: AstrMessageEvent):
    #     """月天数排行榜"""
    #     ranked = self._get_rank(event, "month_days")
    #     msg = ["🏆 本月契约天数榜"] + [
    #         f"{i+1}. 契约者 {data.get('username', '未知')} - {data['month_days']}天"
    #         for i, (uid, data) in enumerate(ranked)
    #     ]
    #     yield event.plain_result("\n".join(msg))
    #
    # @command("签到今日排行榜", alias=["签到今日排行", "签到日排行"])
    # async def today_rank(self, event: AstrMessageEvent):
    #     """今日签到榜"""
    #     ctx_id = _get_context_id(event)
    #     today = datetime.date.today().isoformat()
    #
    #     ranked = sorted(
    #         [(uid, data) for uid, data in self.data.get(ctx_id, {}).items()
    #          if data["last_checkin"] == today],
    #         key=lambda x: x[1]["continuous_days"],
    #         reverse=True
    #     )[:10]
    #
    #     msg = ["🏆 今日契约榜"] + [
    #         f"{i+1}. 契约者 {data.get('username', '未知')} - 连续 {data['continuous_days']}天"
    #         for i, (uid, data) in enumerate(ranked)
    #     ]
    #     yield event.plain_result("\n".join(msg))

    @command_group("超级商店", alias=["shop", "商店"])
    async def shop(self):
        """支持消费战争债券奖章"""

        pass

    @shop.command("重置", alias=["重置"])
    async def shop_reset(self, event: AstrMessageEvent):
        """重置当日C-01申请次数"""
        ctx_id = _get_context_id(event)
        user_id = event.get_sender_id()

        # 初始化数据结构（新增username字段）
        ctx_data = self.data.setdefault(ctx_id, {})
        user_data = ctx_data.setdefault(user_id, {
            "username": event.get_sender_name(),  # 确保存储的是用户昵称
            "total_days": 0,
            "continuous_days": 0,
            "month_days": 0,
            "total_rewards": 0,
            "month_rewards": 0,
            "last_checkin": None
        })

        saving = user_data["month_rewards"]
        if saving < 10:
            yield event.plain_result(f"🛒⛔购买失败。可用奖章不足。")
            return
        else:
            user_data.update({
                "month_rewards": user_data["month_rewards"] - 10
            })

            _save_data(self.data)

            ats = [f"{event.get_sender_id}"]
            chain = event.message_obj.message
            args = event.message_str.split()
            # if len(args) < 2 or not args[1].isdigit():
            #     yield event.plain_result("格式：/重置次数 [QQ号]")
            #     return
            # for comp in chain:
            #     if isinstance(comp, At):
            #         qq = str(comp.qq)
            #         ats.append(qq)
            # if not ats:
            #     yield event.plain_result("请在指令后 @ 一个用户。")
            #     return

            target_id = str(ats[0])
            today = datetime.now().strftime("%Y-%m-%d")
            group_id = str(event.message_obj.group_id)

            if group_id in self.operation_counter and today in self.operation_counter[group_id]:
                if target_id in self.operation_counter[group_id][today]:
                    del self.operation_counter[group_id][today][target_id]
                    self._save_operation_counter()

            yield event.plain_result(f"已重置公民 {target_id} 的当日申请次数")
