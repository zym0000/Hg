# Hg

[English](README.md) | [中文](README.zh.md)

基于 ReAct 模式的异步 Python 编码 Agent 框架。通过 think → act → observe
循环驱动 LLM,支持技能渐进披露、危险工具人工审批、上下文压缩、JSONL
会话持久化与分叉。

## 快速开始

```bash
git clone <repo> && cd Hg
pip install -r requirements.txt   # 或手动安装依赖
python main.py                    # 启动交互式 CLI
```

首次启动会装配 LLM 客户端(默认 `deepseek-v4-flash`,端点
`https://api.deepseek.com`),从 `./skills` 加载技能,在 `./sessions/`
创建会话,注册内建工具,然后进入交互界面。

多行输入:`Shift+Enter`(或 `Ctrl+J`)。

## 核心特性

- **ReAct 循环** — `IDLE → THINKING → PARSING → VALIDATING → ACTIVE → OBSERVING → APPROVAL_WAITING → FINISHED`,支持 cancel / abort 传播。
- **内建工具** — `bash`、`read`、`write`、`edit`、`ls`、`find`、`grep`。
- **技能渐进披露** — 非门控工具始终可见;门控工具在调用 `activate_skill` 后才出现。
- **人工审批门** — 标记为 `dangerous=True` 的工具阻塞等待审批,超时视为拒绝。
- **反振荡 watchdog** — 滑动窗口检测,提前打断工具调用死循环。

## 启动参数

| 参数 | 说明 |
|------|------|
| `--cwd <path>` | 强制指定内建工具的工作目录。 |
| `--resume [id]` | 恢复会话;不传 id 则列出后选择。 |
| `--continue-last` | 恢复最近修改过的会话。 |
| `--no-drift-check` | 恢复会话时若 cwd 已不存在,不报错。 |

## 会话内命令

| 命令 | 作用 |
|------|------|
| `/help` | 列出所有 slash 命令。 |
| `/tools` | 列出已注册工具。 |
| `/status` | 显示会话 id、模型、lane、leaf、entry 数。 |
| `/session` | 会话详细信息。 |
| `/cancel` | 中止当前运行。 |
| `/compact [hint]` | 触发上下文压缩。 |
| `/fork [label]` | 在当前 leaf 分叉会话。 |
| `/new` | 开启新会话。 |
| `/resume` | 列出并切换会话。 |
| `/quit` | 退出。 |

其余输入直接发送给 Agent。

## 配置

编辑 `harness.yaml`:

```yaml
llm:
  api_key: "sk-..."          # 也可设环境变量 AGENT_LLM_API_KEY
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-v4-flash"

paths:
  skills_dir: "./skills"
  sessions_dir: "./sessions"
```

任意 OpenAI 兼容端点均可使用。

## 许可证

MIT。
