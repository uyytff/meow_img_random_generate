import re
import aiohttp
import asyncio
import time
from urllib.parse import urlencode, urljoin
from astrbot.api.message_components import Image
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("M猫图生成助手", "Sinofuma",
          "喵～用 /img 命令获取随机setu喵～",
          "1.1")
class SetuPlugin(Star):
    # 新增：AND/OR 限制常量
    MAX_AND = 3          # 最多用空格分隔的 AND 条件
    MAX_OR = 20          # 每个 OR 组内最多标签数

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.r18 = config.get("default_r18", 0)
        self.tag = config.get("default_tag", "")          # 默认标签（无参时使用）
        self.excludeAI = config.get("default_excludeAI", 1)
        self.num = config.get("default_num", 1)
        self.uid = config.get("default_uid", "")
        self.size = config.get("default_size", "original")
        self.dsc = config.get("default_dsc", 1)
        self.api_url = config.get("api_url", "https://api.lolicon.app/setu/v2")
        self.fuzzy_search = config.get("fuzzy_search", 0)
        self.request_delay = config.get("request_delay", 300)
        self.request_timeout = config.get("request_timeout", 15)
        self.max_retries = config.get("max_retries", 3)
        self.proxy = config.get("proxy", "i.pixiv.re")
        self._cooldowns = {}

    # ========== 原有工具方法 ==========
    def _build_tags(self, tag_str: str) -> str:
        """将配置中的标签字符串（分隔符：逗号、空格、|等）转成用 | 连接的字符串"""
        if not tag_str:
            return ""
        tags = [t.strip() for t in re.split(r'[,，|、\s]+', tag_str) if t.strip()]
        return "|".join(tags)

    def _is_admin(self, event) -> bool:
        return hasattr(event, 'is_admin') and event.is_admin()

    # ========== 新增：解析命令参数 ==========
    def _parse_img_tags(self, text: str):
        """
        解析 /img 后用户输入的标签字符串
        返回: (tag_list, warnings, error)
            tag_list: list | None   （None表示无参数，应使用默认配置；否则为标签列表）
            warnings: list[str]     截断警告列表
            error: str | None       致命错误（如 AND 超限）
        """
        text = text.strip()
        if not text:
            return None, [], None   # 无参数 -> 使用默认配置

        parts = text.split()
        # AND 条件数量检查
        if len(parts) > self.MAX_AND:
            return None, [], (
                f"❌ 最多支持 {self.MAX_AND} 个 AND 条件（用空格分隔），"
                f"你输入了 {len(parts)} 个喵……"
            )

        warnings = []
        tag_list = []
        for i, p in enumerate(parts, 1):
            if '|' in p:
                or_tags = [t.strip() for t in p.split('|') if t.strip()]
                if len(or_tags) > self.MAX_OR:
                    warnings.append(
                        f"⚠️ 第 {i} 个 OR 标签组「{p[:20]}…」包含 {len(or_tags)} 个标签，"
                        f"上限为 {self.MAX_OR}，已自动截断"
                    )
                    or_tags = or_tags[:self.MAX_OR]
                p_clean = "|".join(or_tags)
                if p_clean:
                    tag_list.append(p_clean)
                # 若过滤后为空，则忽略该组（不加入条件）
            else:
                if p.strip():
                    tag_list.append(p.strip())

        return tag_list, warnings, None

    # ========== 修改后的请求逻辑 ==========
    async def _do_request(self, event, custom_tag_list=None, warnings=None):
        """
        custom_tag_list: 用户命令解析出的标签列表 (list of str)，None 表示用默认配置
        warnings:        OR 超限等警告列表，将在请求前发送
        """
        t_start = time.time()
        # 先发送警告（如果有）
        if warnings:
            yield event.plain_result("\n".join(warnings))

        # 根据是否有自定义标签，决定 tag/keyword 参数
        keyword = None
        tag_params = None

        if custom_tag_list is not None:
            # 用户输入了标签
            if custom_tag_list:
                # 模糊搜索仅当：单个标签、不含 | 且配置 fuzzy_search 开启
                if len(custom_tag_list) == 1 and '|' not in custom_tag_list[0] and self.fuzzy_search:
                    keyword = custom_tag_list[0]
                    tag_params = None
                else:
                    tag_params = custom_tag_list   # 列表，稍后通过 doseq=True 展开
                    keyword = None
            # 如果 custom_tag_list 为空（用户输入了 /img 但无有效标签），则沿用默认配置
            else:
                tag_parsed = self._build_tags(self.tag)
                if self.fuzzy_search and tag_parsed and "|" not in tag_parsed:
                    keyword = tag_parsed
                    tag_params = None
                else:
                    tag_params = tag_parsed if tag_parsed else None
        else:
            # 无参数，使用默认配置（完全保留原有逻辑）
            tag_parsed = self._build_tags(self.tag)
            if self.fuzzy_search and tag_parsed and "|" not in tag_parsed:
                keyword = tag_parsed
                tag_parsed = ""
            tag_params = tag_parsed if tag_parsed else None
            keyword = keyword if keyword else None

        # 构建请求参数
        params = {
            "r18": self.r18,
            "num": self.num,
            "excludeAI": str(self.excludeAI == 1).lower(),
            "dsc": str(self.dsc == 1).lower(),
        }
        if tag_params:
            params["tag"] = tag_params   # 字符串或列表，doseq=True 时列表会变成多个 tag=...
        if keyword:
            params["keyword"] = keyword
        if self.uid:
            uid_list = [int(u) for u in re.split(r'[,，\s]+', str(self.uid)) if u.strip().isdigit()]
            if uid_list:
                params["uid"] = uid_list
        size_list = [s.strip() for s in re.split(r'[,，\s]+', self.size) if s.strip()] or ["original"]
        params["size"] = size_list
        if self.proxy:
            params["proxy"] = self.proxy

        query_string = urlencode(params, doseq=True)
        url = urljoin(self.api_url, "?" + query_string)

        # 重试请求
        data = None
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=self.request_timeout)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            raise aiohttp.ClientError(f"HTTP {resp.status}")
                        data = await resp.json()
                        break
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    await asyncio.sleep(1)

        if data is None:
            yield event.plain_result(f"呜…请求失败了喵…{last_exc}")
            return
        if data.get("error"):
            yield event.plain_result(f"API闹脾气了喵…{data['error']}")
            return

        images = data.get("data", [])
        if not images:
            yield event.plain_result("一张图都没找到喵…")
            return

        chain = []
        for img in images:
            img_url = img.get("urls", {}).get(size_list[0] if size_list else "original", "")
            if img_url:
                chain.append(Image.fromURL(img_url))
        if not chain:
            yield event.plain_result("图片不肯出来了喵…")
            return

        yield event.chain_result(chain)
        elapsed = time.time() - t_start
        yield event.plain_result(f"叼来{len(chain)}张图，才用{elapsed:.1f}秒…主人不夸夸我喵？")

    # ========== 冷却检查 ==========
    def _check_cooldown(self, event) -> bool:
        if self.request_delay <= 0:
            return True
        if self._is_admin(event):
            return True
        user_id = event.get_sender_id() if hasattr(event, 'get_sender_id') else "unknown"
        now = time.time()
        last = self._cooldowns.get(user_id, 0)
        if now - last < self.request_delay:
            return False
        self._cooldowns[user_id] = now
        return True

    # ========== 命令入口 ==========
    @filter.command("img")
    async def img_command(self, event: AstrMessageEvent):
        if not self._check_cooldown(event):
            user_id = event.get_sender_id() if hasattr(event, 'get_sender_id') else "unknown"
            remaining = self.request_delay - (time.time() - self._cooldowns.get(user_id, 0))
            yield event.plain_result(f"身体还在发烫…再等{remaining:.0f}秒喵")
            return

        # 提取 /img 后的参数
        message = event.message_str.strip()
        if message.startswith("/img"):
            arg = message[4:].strip()
        else:
            arg = ""

        tag_list, warnings, error = self._parse_img_tags(arg)
        if error:
            yield event.plain_result(error)
            return

        # 传入自定义标签（None 表示用默认配置）
        async for msg in self._do_request(event, custom_tag_list=tag_list, warnings=warnings):
            yield msg
