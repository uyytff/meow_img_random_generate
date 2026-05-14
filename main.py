import re
import aiohttp
import asyncio
import time
from urllib.parse import urlencode, urljoin
from astrbot.api.message_components import Image
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

@register("meow~猫图随机助手", "Sinofuma",
          "喵～用 /img 命令获取随机色图喵～",
          "1.2")
class SetuPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.r18 = config.get("default_r18", 0)
        self.tag = config.get("default_tag", "")
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

    def _build_tags(self, tag_str: str) -> str:
        if not tag_str:
            return ""
        tags = [t.strip() for t in re.split(r'[,，|、\s]+', tag_str) if t.strip()]
        return "|".join(tags)

    def _is_admin(self, event) -> bool:
        return hasattr(event, 'is_admin') and event.is_admin()

    async def _do_request(self, event, override_tags=None):
        t_start = time.time()
        tag_list = []
        keyword = ""
        use_fuzzy = False

        if override_tags is not None:
            tag_list = override_tags
            use_fuzzy = False
        else:
            tag_parsed = self._build_tags(self.tag)
            if self.fuzzy_search and tag_parsed and "|" not in tag_parsed:
                keyword = tag_parsed
                tag_parsed = ""
            if tag_parsed:
                tag_list = [tag_parsed]
            use_fuzzy = self.fuzzy_search
            keyword = keyword

        params = {
            "r18": self.r18,
            "num": self.num,
            "excludeAI": str(self.excludeAI == 1).lower(),
            "dsc": str(self.dsc == 1).lower(),
        }
        if tag_list:
            params["tag"] = tag_list
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

    @filter.command("img")
    async def img_command(self, event: AstrMessageEvent):
        if not self._check_cooldown(event):
            user_id = event.get_sender_id() if hasattr(event, 'get_sender_id') else "unknown"
            remaining = self.request_delay - (time.time() - self._cooldowns.get(user_id, 0))
            yield event.plain_result(f"身体还在发烫…再等{remaining:.0f}秒喵")
            return

        # 获取消息内容（可能包含或不包含 /img 前缀）
        msg = event.message_str.strip()
        # 兼容两种形式："/img tag1 tag2|tag3" 或 "tag1 tag2|tag3"
        if msg.lower().startswith('/img'):
            param_str = msg[4:].strip()   # 去掉 /img，保留后面的部分
        else:
            param_str = msg               # 已经是纯参数

        user_tags = None
        if param_str:
            user_tags = re.split(r'\s+', param_str)
            user_tags = [t for t in user_tags if t]
            if not user_tags:
                user_tags = None

        async for result in self._do_request(event, override_tags=user_tags):
            yield result
