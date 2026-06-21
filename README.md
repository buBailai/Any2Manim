<div align="center">

<img src="frontend/logo.png" width="84" alt="Any2Manim">

# Any2Manim

**用一句话，把任何学科的讲解思路变成教学动画视频。**

老师用自然语言描述 → AI 拆分镜、写 [Manim](https://www.manim.community/) 代码、自动渲染 → 对话式修改 → 配音字幕 → 导出。

开源 · 自带 API Key（BYO-Key）· 数据本地自主 · 一键启动

<p>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-brightgreen" alt="MIT License"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/version-v1.0.3-5B5BD6" alt="v1.0.3">
  <img src="https://img.shields.io/badge/manim-CE%200.20-orange" alt="ManimCE 0.20">
</p>

</div>

---

## ✨ 这是什么

Any2Manim 让**不会写代码的老师**也能做出专业的教学动画：你只要描述「画一个自由落体的小球，旁边同步画出速度-时间图像」，它就会自动生成、渲染并预览这段动画，你还能像聊天一样继续改（「把小球改成红色」「速度图线再画粗一点」），满意后导出高清视频，可选 AI 配音和字幕。

用途：课堂投屏 · 给课件/作业配讲解视频 · 备课素材。

> 部署哲学仿照 [OpenMentor](https://github.com/buBailai/OpenMentor)：开源、自带 Key、本地部署、数据自主。
> 教学生成质量上借鉴了 [Math-To-Manim](https://github.com/HarleyCoops/Math-To-Manim) 的「先教学后符号 / 镜头即叙事 / 公式即角色」理念（见下方「核心特性 · 教学优先」与[致谢](#致谢)）。

## 🎬 核心特性

- **自然语言生成动画** —— 描述即生成，先分镜后写码，渲染引擎是 [ManimCE](https://www.manim.community/)（社区版）。
- **教学优先的生成管线** —— 不是「一句话直接出代码」，而是先产出**结构化教学镜头计划**（先建立直觉、再引入符号；每镜头有明确「教什么」；公式自动拆项讲解），再忠实翻译成动画，并做「能教」质检。借鉴自 Math-To-Manim。
- **自愈渲染循环** —— 代码渲染报错时自动把错误喂回模型修复，有界重试、会喊停、保住最后一个可渲版本，不把报错甩给老师。
- **对话式定向编辑** —— 修改只产生最小代码改动，又快又稳。
- **好默认观感** —— 内置深色画布、统一色板/字体/留白、组件库，出片即好看。
- **多格式导出** —— MP4（720p/1080p/4K）、GIF、封面图（可拖进度条任意选帧）。
- **AI 配音 + 字幕** —— edge-tts 多音色、可调语速，AI 按分镜时长自动写旁白，字幕可烧录或外挂。
- **素材上传 / 分学科示例库 / 项目归档 / 版本回溯**。
- **深色模式 · 简繁切换 · 局域网访问**。
- **演示模式** —— 不填 Key 也能用内置范例离线出片，先看效果。

## 🚀 快速开始

### 环境要求

- Python 3.10+（开发于 3.14）
- 渲染依赖：`ffmpeg` 由 `static-ffmpeg`（pip 自带，含字幕烧录所需 libass）解决；含 LaTeX 公式的动画需本机有 TeX 环境（如 TinyTeX / TeX Live）。

### 安装与启动

```bash
# 1. 建虚拟环境并装依赖
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. 启动（绑定 0.0.0.0，支持局域网访问）
./start.sh                       # 或：python -m uvicorn backend.main:app --host 0.0.0.0 --port 8848
```

浏览器打开 <http://localhost:8848> 即可。局域网内其他设备用启动时打印的 IP 访问。

### 配置 AI 模型（BYO-Key）

点右上角 ⚙️ 设置，选厂商填自己的 API Key。内置 DeepSeek、豆包·火山方舟、通义千问、智谱 GLM、硅基流动、Cherry Studio 企业版、Kimi、OpenAI、Ollama（无需 Key）、自定义（OpenAI 兼容）等预设。

> 不填 Key 也能用：**演示模式**会用内置范例离线出片。

## 🧱 技术架构

| 层 | 选型 |
|---|---|
| 后端 | FastAPI + SQLite + asyncio 单 worker 渲染队列 |
| 渲染 | ManimCE 0.20.1（子进程）+ static-ffmpeg |
| 前端 | 纯 HTML / CSS / JS，无构建步骤；SSE 实时进度 |
| 配音 | edge-tts（仅导出时合成） |
| 存储 | 代码廉价、视频昂贵 —— 长期只存代码 + 元数据 + 缩略图，版本回溯靠代码重渲 |

```
app/
├── backend/        # FastAPI 后端：引擎/渲染/自愈/配音/队列
│   └── prompts/    # 注入提示词（API 速查/避坑清单）
├── frontend/       # 纯前端工作区（对话/预览/代码/状态/字幕）
├── tools/          # gen_s2t.py 等开发工具
├── requirements.txt
├── start.sh
├── CHANGELOG.md    # 版本号 + 更新日志的唯一来源（页面动态读取）
└── README.md
```

## 🛠 开发说明

- **更新日志 / 版本号**：左上角版本号与右上角「更新日志」浮窗都读取 `CHANGELOG.md`，发版只需更新该文件，无需改页面代码。
- **简繁词表**：界面文案改动后，重跑 `python tools/gen_s2t.py`（需 `pip install opencc-python-reimplemented`）重新生成 `frontend/s2t.js`。仅开发期需要，非运行时依赖。

## 致谢

- [OpenMentor](https://github.com/buBailai/OpenMentor) —— 部署哲学（开源 / 自带 Key / 本地部署 / 数据自主）。
- [Math-To-Manim](https://github.com/HarleyCoops/Math-To-Manim) —— 教学生成管线的理念来源：先教学后符号、镜头即叙事、公式即角色、artifact 可审查可修复。Any2Manim 据此把「描述 → 结构化教学计划 → 忠实翻译成代码 → 能教质检」拆成可控的一条链路（按教师产品的延迟 / 模型 / 机器现实做了收敛，未照搬其多 agent 流水线）。
- [ManimCE](https://www.manim.community/) · [edge-tts](https://github.com/rany2/edge-tts) · [static-ffmpeg](https://github.com/zackees/static_ffmpeg) —— 渲染、配音、媒体处理基础设施。

## 📜 许可证

[MIT](LICENSE) © buBailai

> 渲染引擎 ManimCE、TTS edge-tts、static-ffmpeg 等依赖各自遵循其开源许可。

## ⭐ Star 趋势

[![Star History Chart](https://api.star-history.com/svg?repos=buBailai/Any2Manim&type=Date)](https://star-history.com/#buBailai/Any2Manim&Date)

> 如果 Any2Manim 帮到了你，欢迎点个 ⭐ Star 让更多老师看到。
