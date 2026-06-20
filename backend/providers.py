"""LLM 厂商预设（对齐 OpenMentor：多厂家 + Ollama + 自定义，均 OpenAI 兼容）。

base_url 存到 /v1 级别（不含 /chat/completions），由 OpenAILLM 统一拼 /chat/completions。
needs_key=False 的本地厂商（Ollama）无需 Key 即视为已配置。
"""
from __future__ import annotations

# key → 预设。base_url 留空表示要用户自己填（custom）。
PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat", "needs_key": True,
        "hint": "代码生成推荐，便宜稳。模型填 deepseek-chat 或更强版本。"},
    "doubao": {
        "label": "豆包 / 火山方舟", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "", "needs_key": True,
        "hint": "模型名填你在方舟创建的接入点 endpoint id 或官方模型名。"},
    "qwen": {
        "label": "阿里通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus", "needs_key": True,
        "hint": "DashScope OpenAI 兼容模式。模型如 qwen-plus / qwen-max。"},
    "glm": {
        "label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4", "needs_key": True,
        "hint": "模型如 glm-4 / glm-4-plus。"},
    "siliconflow": {
        "label": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "Qwen/Qwen2.5-72B-Instruct", "needs_key": True,
        "hint": "聚合多家开源模型，模型名用完整路径如 deepseek-ai/DeepSeek-V3。"},
    "cherry": {
        "label": "Cherry Studio 企业版", "base_url": "https://express-ent-admin.cherryin.ai/v1",
        "default_model": "deepseek/deepseek-v4-flash", "needs_key": True,
        "hint": "OpenAI 兼容网关。学校自部署的话把 Base URL 改成自己的服务端地址（填到 /v1）。"},
    "moonshot": {
        "label": "月之暗面 Kimi", "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k", "needs_key": True,
        "hint": "模型如 moonshot-v1-8k / moonshot-v1-32k。"},
    "openai": {
        "label": "OpenAI", "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o", "needs_key": True,
        "hint": "需可访问 openai.com。模型如 gpt-4o。"},
    "ollama": {
        "label": "Ollama 本地", "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.2", "needs_key": False,
        "hint": "本地零成本，无需 Key。先 ollama pull 一个模型（如 qwen2.5-coder）。代码生成建议用 coder 类模型。"},
    "custom": {
        "label": "自定义 (OpenAI 兼容)", "base_url": "",
        "default_model": "", "needs_key": True,
        "hint": "任意 OpenAI 兼容端点。Base URL 填到 /v1 级别，会自动拼 /chat/completions。"},
}


def public_list() -> list[dict]:
    """给前端的精简列表（含顺序）。"""
    return [{"key": k, **{kk: vv for kk, vv in v.items()}} for k, v in PROVIDERS.items()]


def get(key: str) -> dict | None:
    return PROVIDERS.get(key)
