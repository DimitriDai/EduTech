# -*- coding: utf-8 -*-
"""
deepseek_client.py

统一的模型调用封装。
extract/enrich/grading 都会复用它，避免每个模块各写一套请求。

你当前遇到的问题：
- 用 https://api.deepseek.com/v1 + "/chat/completions" 在你这边实测会 404
- 但 DeepSeek 官方 curl 示例路径是：https://api.deepseek.com/chat/completions
所以本脚本实现了：
1) 你传入 base_url 不管是：
   - https://api.deepseek.com
   - https://api.deepseek.com/v1
   - https://api.deepseek.com/v1/chat/completions
   - https://api.deepseek.com/chat/completions
   都能自动归一化到正确 endpoint
2) 捕获 HTTPError 并打印 error body（否则永远看不到 400/401 的真实原因）
3) 404 时自动 fallback：从 /v1/chat/completions 退回 /chat/completions
"""

from __future__ import annotations

import os
import json
import time
import urllib.request
import urllib.error
from typing import Optional


class DeepSeekClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 60,
        retries: int = 1,
        retry_backoff_sec: float = 1.0,
    ):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        # 兼容你之前的变量名：DEEPSEEK_API_URL / DEEPSEEK_BASE_URL
        self.base_url = base_url or os.getenv("DEEPSEEK_API_URL") or os.getenv("DEEPSEEK_BASE_URL")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        self.timeout = int(timeout)
        self.retries = int(retries)
        self.retry_backoff_sec = float(retry_backoff_sec)

        # ---- 严格校验（上线级）----
        if not self.api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Please set it as an environment variable."
            )

        if not self.base_url:
            raise RuntimeError(
                "DEEPSEEK_BASE_URL (or DEEPSEEK_API_URL) is not set.\n"
                "Recommended:\n"
                "  https://api.deepseek.com\n"
                "or (OpenAI SDK compatible):\n"
                "  https://api.deepseek.com/v1\n"
            )

        if not self.model:
            raise RuntimeError("未设置 DEEPSEEK_MODEL 环境变量（例如 deepseek-chat）")

    @staticmethod
    def _normalize_base_url(u: str) -> str:
        """
        把用户传入的各种写法归一化成“站点根”：
        - https://api.deepseek.com
        - https://api.deepseek.com/v1
        - https://api.deepseek.com/chat/completions
        - https://api.deepseek.com/v1/chat/completions
        归一化后返回：
        - https://api.deepseek.com
        - 或 https://api.deepseek.com/v1  （如果用户明确给的是 /v1 且不是具体 endpoint）
        """
        u = (u or "").strip().rstrip("/")

        # 如果用户直接给了 endpoint（带 /chat/completions），先去掉末尾 endpoint
        if u.endswith("/chat/completions"):
            u = u[: -len("/chat/completions")].rstrip("/")

        return u

    @staticmethod
    def _build_endpoints(base_url: str) -> list[str]:
        """
        基于 base_url，生成候选 endpoints（按优先级）：
        - 如果 base_url 以 /v1 结尾：先试 /v1/chat/completions，再 fallback 到 /chat/completions
        - 否则：直接用 /chat/completions
        """
        b = DeepSeekClient._normalize_base_url(base_url)

        endpoints: list[str] = []
        if b.endswith("/v1"):
            endpoints.append(b + "/chat/completions")  # OpenAI 兼容风格
            endpoints.append(b[:-3].rstrip("/") + "/chat/completions")  # fallback 到官方 curl 风格
        else:
            endpoints.append(b + "/chat/completions")

        # 去重保持顺序
        seen = set()
        uniq = []
        for e in endpoints:
            if e not in seen:
                uniq.append(e)
                seen.add(e)
        return uniq

    def call_model(self, prompt: str, max_tokens: int = 300, temperature: float = 0.2) -> str:
        """
        返回模型原始文本输出（string）
        使用 chat/completions 兼容结构：choices[0].message.content
        """
        # ---- 强制校验 max_tokens（DeepSeek: 1 ~ 8192）----
        raw_max_tokens = max_tokens
        try:
            mt = int(max_tokens)
        except Exception:
            mt = 300

        if mt < 1:
            mt = 1
        elif mt > 8192:
            mt = 8192

        max_tokens = mt

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        endpoints = self._build_endpoints(self.base_url)

        last_exc: Optional[Exception] = None

        # endpoints 逐个尝试；每个 endpoint 支持简单重试（针对 429/5xx/网络抖动）
        for endpoint in endpoints:
            for attempt in range(self.retries + 1):
                req = urllib.request.Request(
                    endpoint,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    method="POST",
                )

                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        body = resp.read().decode("utf-8", errors="replace")
                        obj = json.loads(body)

                    try:
                        return obj["choices"][0]["message"]["content"]
                    except Exception:
                        # 如果返回结构不符合预期，直接把原始 body 抛出去，方便你定位
                        raise RuntimeError(f"DeepSeek response format unexpected. body={body[:2000]}")

                except urllib.error.HTTPError as e:
                    err_body = ""
                    try:
                        err_body = e.read().decode("utf-8", errors="replace")
                    except Exception:
                        err_body = "<failed to read error body>"

                    # 先把真实错误打印出来（你定位用）
                    print("[DEEPSEEK_HTTP_ERROR]", "endpoint=", endpoint, "code=", e.code, "body=", err_body)

                    # 404：直接换下一个候选 endpoint（通常是 /v1/... 不存在）
                    if e.code == 404:
                        last_exc = RuntimeError(f"DeepSeek HTTP 404 at {endpoint}: {err_body}")
                        break  # break retry loop -> try next endpoint

                    # 400/401：请求不合法 / key 不对，重试无意义
                    if e.code in (400, 401, 403):
                        raise RuntimeError(f"DeepSeek HTTP {e.code}: {err_body}")

                    # 429/5xx：可能限流或服务波动，可重试
                    last_exc = RuntimeError(f"DeepSeek HTTP {e.code}: {err_body}")
                    if attempt < self.retries:
                        time.sleep(self.retry_backoff_sec * (2 ** attempt))
                        continue
                    break  # out retry loop -> next endpoint

                except Exception as e:
                    print("[DEEPSEEK_UNKNOWN_ERROR]", "endpoint=", endpoint, "err=", repr(e))
                    last_exc = e
                    if attempt < self.retries:
                        time.sleep(self.retry_backoff_sec * (2 ** attempt))
                        continue
                    break  # out retry loop -> next endpoint

        # 所有 endpoint 都失败
        raise last_exc if last_exc else RuntimeError("DeepSeek call failed with unknown reason.")