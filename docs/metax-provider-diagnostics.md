# MetaX 原生结构化输出与工具调用排查

验证日期：2026-11-13 至 2026-11-15。接入配置见 [JIRA agent 部署说明](jira-agent.md#metax-自建模型网关)。

已确认 MetaX API 在同时启用工具与原生 JSON 输出约束时不能完成本次探针任务。直接 HTTP 请求就能复现，不需要网站、Codex、业务提示词或完整 JIRA Schema。**尚未定位到服务端具体代码，不能将其定性为模型权重问题，也不能断言由网关转换导致。**

## 实测结果

测试工具 `read_probe` 不执行系统命令，也不读写业务文件。只有收到有效工具调用后，测试程序才生成一个随机值并返回。任务要求将该值作为最终 JSON 的 `value`。通过条件是实际调用工具且最终值完全匹配；HTTP 200、合法 JSON、文本中出现工具名字都不算通过。

A/B 请求的模型、任务、工具和 `tool_choice=auto` 保持相同，只改变原生输出格式字段。Responses 使用 `text.format`，Chat Completions 使用 `response_format`。下表为非流式 HTTP 实测：

| 服务 / 模型 | 接口 | 不带原生格式约束 | 带原生 JSON Schema |
| --- | --- | --- | --- |
| MetaX / GLM-5.3 | `/responses` | 调用工具，随机值匹配 | 0 次工具调用，失败 |
| MetaX / GLM-5.3 | `/chat/completions` | 调用工具，随机值匹配 | 0 次工具调用，失败 |
| MetaX / deepseek-v4-flash | `/responses` | 调用工具，随机值匹配 | 0 次工具调用，失败 |
| MetaX / deepseek-v4-flash | `/chat/completions` | 调用工具，随机值匹配 | 0 次工具调用，失败 |
| DeepSeek 官方 / deepseek-flash | `/responses` | 调用工具，随机值匹配 | 调用工具，随机值匹配 |

两侧 DeepSeek 的模型标识不同，未取得实际模型版本信息，不能据此假定模型权重完全相同。

补充控制实验：

- 在 MetaX Chat 接口将格式改为 `json_object`、将 Schema 的 `strict` 改为 `false`，两个模型仍未调用工具。
- 保留 Schema，将首轮 `tool_choice` 改为 `required` 或指定 `read_probe`，两个模型仍未返回协议中的工具调用。
- 去掉所有工具、只要求返回 `{"value":"control-ok"}`：MetaX DeepSeek 的原生 Schema 控制通过；GLM 返回了不完整或非法 JSON。GLM 的结构化输出本身也需要排查。
- 最初使用 `high` 推理强度；用仓库探针不传推理强度重测上述基础组合，结论一致。因此不是仅由客户端显式设置 `high` 触发。
- MetaX 响应头报告 `x-litellm-version: 1.101.3`，前置服务器报告 Kong。响应头无法说明实际模型推理引擎及其版本。

继续使用现有调用权限测试，未取得服务端配置：

- 将 `parallel_tool_calls` 设为 `false`、工具定义的 `strict` 设为 `false`、推理强度改为 `low`，均未恢复组合请求的工具调用。
- MetaX DeepSeek 的 `reasoning.effort=none` 或顶层 `thinking.type=disabled` 返回中仍报告思考 token，不能据此认为关闭成功。通过 LiteLLM `extra_body` 传入 `thinking.type=disabled` 或 `chat_template_kwargs.enable_thinking=false` 后，返回不再有思考内容/思考 token，但仍未调用工具。
- GLM 在 `extra_body.separate_reasoning=false` 时返回合并的文本，原生 Schema 下仍出现损坏的内容或工具标记，不能靠切换思考文本分离方式解决。
- 网关错误正文提示了备用模型组 `inc/deepseek-v4-flash`；用现有 Key 直接请求返回 403，提示该账号仅允许两个已公开模型。该备用模型未做功能验证。

## 模型目录对照

`metax-models.json` 与现有 `deepseek-models.json` 的能力配置并不完全相同。MetaX 目录使用较小的上下文范围、仅开放文本输入，并关闭了未验证的图片、搜索、自由格式补丁和并行工具能力；基础指令也更短。

为了核对目录是否造成“不执行命令”，使用同一 Codex 0.162.1、同一完整 JIRA Schema、同一文件读写任务，在一次性隔离容器中分别加载：

1. 当前 MetaX 目录。
2. 正常工作的 DeepSeek 目录副本，保留全部能力和指令配置，仅替换模型标识与显示名。

| 模型 | 当前 MetaX 目录 | DeepSeek 目录副本 |
| --- | --- | --- |
| GLM-5.3 | 0 次命令，文件未改，最终 JSON 非法 | 0 次命令，文件未改，最终 JSON 非法 |
| deepseek-v4-flash | 0 次命令，文件未改，最终 JSON 合法 | 0 次命令，文件未改，最终 JSON 合法 |

替换目录不能修复本次故障；而直接 HTTP 复现根本不读取模型目录。这两组证据不支持将故障归因于 `metax-models.json`。目录中的能力元数据仍须按实际服务能力维护，不能为了尝试解决问题而直接对外宣称支持全部 DeepSeek 能力。

此前真实 Codex 0.162.1 的隔离文件操作测试也有同样现象：MetaX 配合原生 `outputSchema` 没有修改测试文件，直连 DeepSeek 则实际执行了命令并修改文件。非流式直接 HTTP 的复现说明，问题不只发生在 SSE 处理或 Codex 客户端中。Chat 接口同样失败，说明只检查 Responses 转 Chat 的转换还不够。

## 可直接运行的最小复现

[tools/probe_llm_structured_tools.py](../tools/probe_llm_structured_tools.py) 只需要 Python 3.10+ 标准库。没有 SDK、业务配置或网站依赖，不运行模型生成的系统命令。Key 从环境变量读取，不作为命令行参数，也不输出服务器错误正文。

在能访问网关的机器上，通过私有环境配置提供 `LLM_API_KEY`，然后从仓库根目录运行：

```bash
python3 tools/probe_llm_structured_tools.py \
  --base-url https://llm-gw.kai.metax-tech.com/v1 \
  --model GLM-5.3 --model deepseek-v4-flash \
  --effort high --include-schema-only --no-proxy
```

默认对 Responses 和 Chat 两个接口分别运行“不带格式约束”和“带原生 Schema”两组测试；`--include-schema-only` 另加不带工具的纯 Schema 控制。`--no-proxy` 适用于本次验证环境的网关直连需求；需要代理的环境应去掉它。不指定 `--effort` 可验证服务端默认行为。

每行输出一个 JSON 结果。工具测试通过时必须同时看到 `tool_count > 0`、`value_verified: true` 和 `passed: true`。纯 Schema 控制不要求工具调用。退出码 0 表示全部通过，1 表示至少一组未通过，2 表示参数或凭据配置错误。实际请求会产生少量模型调用费用。

其他诊断选项：

```bash
# 只测 Chat 的原生 Schema，并要求首轮必须调用工具。
python3 tools/probe_llm_structured_tools.py \
  --base-url https://llm-gw.kai.metax-tech.com/v1 \
  --model deepseek-v4-flash --endpoint chat --format schema \
  --tool-choice required --no-proxy

# 导出不含凭据的首轮请求；不联网、不需要 Key。
python3 tools/probe_llm_structured_tools.py \
  --base-url https://example.invalid/v1 --model example-model \
  --include-schema-only --print-requests
```

`required` / `named` 仅是首轮诊断选项，收到工具结果后会恢复 `auto`，不作为业务修复方案。探针最多请求三轮。

## 已验证的客户端兼容方案：结果工具

2026-11-13 首次接入时保留 `metax-models.json`，将完整结果 Schema 放入 `submit_result` 函数的参数定义，执行轮次不传 `outputSchema`。模型正常调用 shell 完成任务，再调用 `submit_result` 提交结论；客户端按原 Schema 校验参数，非法结果返回具体错误供模型修正。Schema 不拼进任务提示词。后续的新对话使用下文的简化工具参数，已有嵌套参数对话保持原契约。

此方案使用标准函数调用，Codex app-server 通过 `thread/start.dynamicTools` 声明工具，通过 `item/tool/call` 交给客户端处理。Codex 将该接口标为实验性；仓库通用客户端已有相应能力，知识学习模块也已使用动态工具。[Codex app-server 官方文档](https://learn.chatgpt.com/docs/app-server#dynamic-tool-calls-experimental)

在一次性隔离容器中使用真实 Codex 0.162.1、MetaX 网关和完整 JIRA `RESULT_SCHEMA`，每个模型验证首轮及同一线程续办。每轮由测试程序另写一个随机文件值，任务提示不包含该值；验收要求模型实际读取、记录原值、修改文件并重新读取核验。

| 模型 / 轮次 | 实际 shell 调用 | 文件修改 | 原始随机值核对 | 完整 Schema 校验 |
| --- | --- | --- | --- | --- |
| GLM-5.3 / 首轮 | 3 次，退出码均为 0 | 通过 | 通过 | 通过 |
| GLM-5.3 / 续办 | 3 次，退出码均为 0 | 通过 | 通过 | 通过 |
| deepseek-v4-flash / 首轮 | 3 次，退出码均为 0 | 通过 | 通过 | 首次拒绝后重新提交通过 |
| deepseek-v4-flash / 续办 | 3 次，退出码均为 0 | 通过 | 通过 | 通过 |

DeepSeek 首轮的 `comment.evidence_indices` 超出网站 Schema 的数量限制，客户端返回校验错误后，模型修正并再次提交。当时没有核对 Codex 发出的工具 Schema，不能把这次失败算作服务端 `strict: true` 失效的证据；后续抓包发现 Codex 会丢掉该数量限制，详见下文严格工具参数验证。网站仍需保留完整校验。

探针也提供标准函数结果模式，方便独立核对这条路径：

```bash
python3 tools/probe_llm_structured_tools.py \
  --base-url https://llm-gw.kai.metax-tech.com/v1 \
  --model GLM-5.3 --model deepseek-v4-flash \
  --format tool --effort high --no-proxy
```

`tool` 模式必须收到真实 `read_probe` 调用及携带正确随机值的 `submit_result` 调用才算通过，普通 JSON 文本不算结果工具提交。

上述仓库探针已实测：两个模型在 Responses 和 Chat 接口的四组 `tool` 用例全部通过。

这验证了客户端兼容方向，**不等于原生结构化输出已修复**。网站现已在 MetaX 的 JIRA 正式任务和测试任务中接入此方案：保存经校验的工具参数，处理重连、恢复及重复提交，普通最终消息不作为结果。使用现有事件表，无数据库迁移或新增输出模式配置。知识学习和评测模型的输出协议没有改变，不能据此宣称它们也已解决原生 Schema 问题。

### 实际任务暴露的模型目录加载问题

8013 的 MC3-7488 首次真实验证执行了 69 次命令并生成 16 个文件，但最终 `submit_result` 参数包含非法 JSON `"fix": ,`，Codex 在交给网站前解析失败。随后人工要求仅补交结果，该线程返回空回复。这两轮均记为失败，没有把已生成文件或 HTTP 成功当作执行流程通过。

进一步核对发现，实际会话的上下文为 258,400 tokens，与目录设置的 32K 不符。此前隔离探针在进程启动时指定了目录，网站却按线程传入目录，两者并不等价。Codex 配置源码明确说明：`model_catalog_json` 仅在启动时应用，对话级覆盖是无效操作。[官方配置源码](https://github.com/openai/codex/blob/main/codex-rs/config/src/config_toml.rs)

因此更正此前“刷新目录即可、不需重启”的结论：网站读取目录只证明选择列表正确，不能证明执行端采用了模型能力。B 需要合并默认及第三方目录，在启动配置中设置 `model_catalog_json` 并重启对应 app-server。仓库提供 `deploy/jira-agent/codex/merge-model-catalogs.py`，部署步骤见 [JIRA agent 文档](jira-agent.md#metax-自建模型网关)。网站已移除无效的线程级目录覆盖。

这项配置错误和前述原生 Schema 问题是两个独立问题；直接 HTTP 复现不使用模型目录，不能据此撤销原生兼容问题的证据。对于远端正常结束却没有提交结果的情况，网站还会在同一业务轮次内限一次仅补交已有结果，禁止重做调查和实验，失败则如实报告。

### 8013 实际任务验收（2026-11-13，北京时间）

模型目录开始生效后，32K / 24K 压缩阈值又导致完整技能和工单材料频繁触发压缩、重复读取。该验证轮次主动中断并保留记录；根据真实请求已处理约 91K 输入的证据，将 GLM 客户端窗口调整为 96K、压缩阈值 80,000，并在原对话续办。运行记录确认有效窗口为 93,388 tokens。此值是客户端设置，不是对服务端最大容量的声明；OpenCode 返回的两个模型上下文限制均为 0，不能从该配置推导容量。

实际对话：`jac_cca608991f0b`，工单 MC3-7488，模型 `metax / GLM-5.3 / max`。最终轮次 `jat_33e609d07491` 于 20:39:21 开始、20:53:08 完成。本轮复核上一轮真实调查的 manifest、拉取日志和附件，沿用原调查证据，没有重新下载镜像或发起远程实验。

| 验收项 | 结果 |
| --- | --- |
| 当前轮实际命令 | 网站记录 10 次，退出码均为 0 |
| 结果工具 | 1 次 `submit_result`，完整 Schema 与业务校验通过；本轮无需自动补交 |
| 网站状态 | `completed`，`delivery_status=complete`，无执行错误 |
| 交付文件 | 27 个文件，逐一核对 B 上原文件、网站缓存及数据库 SHA256，一致 |
| 历史证据副本 | 16 个 `history/` 文件均与原调查目录逐字节一致 |
| 浏览器 | 结果页面无脚本错误，报告下载成功 |
| JIRA 写入 | `post_comment=false`，评论事件 `skipped`；未发布评论或修改工单 |

业务结论为 `insufficient_evidence / needs_help`：镜像历史拉取出现连接重置，附件实际是登录 HTML，尚不能验证镜像内原故障，更没有确认修复。**通过的是 Codex + MetaX 执行与交付流程，不是 MC3-7488 缺陷修复**。`max` 档位该轮耗时约 14 分钟，不据此承诺响应速度或所有长任务均可成功。

最终专项回归 29 项通过，覆盖结果校验、重复提交、重连和网站重启、取消、旧线程提示、测试用例续办、一次补交及启动目录合并。验证使用隔离数据库和本机 WebSocket；首次漏配本机 `NO_PROXY` 的运行出现连接失败，修正测试环境后全部通过。8011、8015 网站没有重新部署；共享 Codex 4511 在确认无活动任务后重启加载目录，默认服务商和默认模型保持原值。

## 严格工具参数验证（2026-11-15）

验证目标：完整业务 `RESULT_SCHEMA` 放在 `submit_result.parameters`，设置 `strict: true`，不传 `text.format` / `outputSchema`，能否获得协议层的格式保证、替代当前结果补交。**结论：当前 Codex + MetaX 组合不能靠增加这一字段可靠解决问题。**

官方 strict 模式要求每个对象的 `additionalProperties: false`、所有属性 required；本次完整业务 Schema 满足这两个条件。但 OpenAI 对自身接口的保证不能直接推定第三方实现也支持。[严格函数调用说明](https://developers.openai.com/api/docs/guides/function-calling#strict-mode)

### Codex 实际发出的请求

使用与 4511 二进制 SHA256 相同的 Codex 0.162.1，在一次性容器、独立 `CODEX_HOME` 中启动 app-server，HTTP 接收端只监听容器内回环地址，容器禁用外网。使用假 Key，完整 Schema 经 `thread/start.dynamicTools` 注册；接收端捕获请求后返回预设错误，不进行模型推理。

| 注册参数 | 模型 | 实际 `/v1/responses` 工具定义 |
| --- | --- | --- |
| 不传 `strict` | GLM-5.3 | `strict: false` |
| 额外传 `strict: true` | GLM-5.3 | 仍为 `strict: false` |
| 额外传 `strict: false` | GLM-5.3 | `strict: false` |
| 不传 `strict` | deepseek-v4-flash | `strict: false` |

四组均成功创建线程，但额外字段未生效。Codex 还会丢掉以下三个 Schema 约束，其他内容与输入一致：

- `next_steps.maxItems = 2`
- `comment.evidence_indices.maxItems = 2`
- `comment.evidence_indices.items.minimum = 0`

因此，仅修改网站注册参数或 `config.toml`，并不能使当前动态工具链路发送严格的完整 Schema。要实现这条路径，需要 Codex 提供实际生效的 strict 支持并保留约束；网站本地校验不能省略。

### 绕过 Codex，直接验证 MetaX

使用既有 MetaX 凭据直连 Responses 接口，不经代理、SDK 或 Codex。完整 Schema 直接放入函数参数，`strict: true / false` 做对照，`parallel_tool_calls: false`、`stream: false`、`max_output_tokens: 8192` 保持一致。不发送原生最终输出格式，也没有格式错误后的重试或补交提示。共 28 个测试用例、34 次 HTTP 请求，均为 HTTP 200、`status: completed`；下列失败不是超时、输出截断或 403。

普通任务要求先调用 `read_probe`，收到此时才生成的随机值后，再调用 `submit_result` 并将随机值放入 evidence。`high + auto` 下两个模型 × strict 开/关的四组均完成工具调用，完整 Schema 和随机值校验通过。但是四组都填写了不该填写的 assistance，未通过网站的跨字段业务校验；这些语义规则并未编码进 Schema，不能把它算作 strict 失效，也不能把普通测试写成完整业务验收通过。

为了区分模型自行遵循和服务端强制约束，另用**合成负例**要求逐字提交与 Schema 冲突的参数：非法枚举 `conclusion: "INVALID_CONTROL"`、`fix: ""`、额外的 `debug_extra`、缺少 `machines_used`、三个 `next_steps`。该提示只用于检验约束，不加入网站任务提示。

| 模型 | effort / tool_choice | `strict: true` 完整 Schema 负例结果 |
| --- | --- | --- |
| GLM-5.3 | high / auto | 调用了 `submit_result`，但保留非法枚举、字符串 fix、多余字段和超量 next_steps；Schema 校验失败 |
| GLM-5.3 | high / 指定函数 | 没有返回工具调用；不算通过，也不据此声称生成了违规工具参数 |
| GLM-5.3 | max / auto | 没有返回工具调用；不能作为可用的提交路径 |
| deepseek-v4-flash | high / auto | 本次参数符合完整 Schema |
| deepseek-v4-flash | max / auto | 本次参数符合完整 Schema |
| deepseek-v4-flash | high / 指定函数 | `fix` 变成包含 `{"description": "a"}` 的**字符串**，不是对象；Schema 校验失败 |

`auto` 的完整负例在 strict 关闭时，两个模型的 high / max 四组均生成违规参数。另测只含一个枚举字段的小 Schema：DeepSeek 的 strict 用例通过；GLM 没有返回工具调用。小 Schema 成功不能代替完整业务 Schema 验证，少量正确样本也不能证明服务端始终强制约束。

本次完整输入 Schema 的 SHA256 为 `539ed9562e9971e530b10849c1d18796e3bfac93d34b36183ef8fe30b3dad362`；Codex 转写后的值为 `249d4f745d807ce1a3bb37b3a7bbf6b7fe1eca9163bab5ee406e6b0dcb326d17`。摘要算法为 Python `hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()`，用于区分直接 HTTP 与 Codex 路径的输入。

这些证据确认了两个边界：当前 Codex 动态工具不会启用 strict；当前 MetaX 服务链路也没有对两个模型的完整 Schema 提供可靠保证。不能仅凭客户端结果进一步认定是模型权重、网关转换还是推理引擎的问题。测试没有改变 8013 的运行代码或重启任何业务服务，没有创建任务或写入 JIRA。

## 简化结果工具验证（2026-11-15）

工具将 16 个顶层业务字段中的对象展开为 27 个参数；网站校验参数后还原完整结果，再执行原 Schema 和业务校验。以现有线程事件的 `arguments_format: flat-v1` 判断格式，没有该字段的旧线程仍用嵌套结构。测试任务沿用冻结 Schema，缺项不补默认值，Schema 不加入任务提示。

真实 Codex 0.162.1 对照使用两个模型的 `max` 档位、相同业务指令和一次性容器，仅改变工具参数结构。每线程先修复 Python 加法错误、重跑原测试，再续办缺少数据文件的受阻任务；验收检查命令、随机文件值、产物及完整结果。下表为校验拒绝后重提次数，8 轮最终均通过：

| 模型 / 任务 | 旧结构拒绝后重提次数 | 简化结构拒绝后重提次数 | 最终结果 |
| --- | --- | --- | --- |
| GLM-5.3 / 修复 | 2：assistance 必填字段缺失、机器格式错误 | 1：机器格式错误 | 两版均通过 |
| GLM-5.3 / 受阻续办 | 0 | 0 | 两版均通过 |
| deepseek-v4-flash / 修复 | 1：机器格式错误 | 1：机器格式错误 | 两版均通过 |
| deepseek-v4-flash / 受阻续办 | 1：fix 缺失 | 1：conclusion 枚举错误 | 两版均通过 |

两版各有 1/4 轮首次提交通过，不能据此认定总体成功率提高。简化结构未出现本组的嵌套字段遗漏，语义错误仍需校验反馈；服务端原生 Schema 问题尚未解决。

8013 网站测试沿用完整知识包，同样要求读取随机值、复现和修复加法错误、重跑原测试并交付报告，没有人工追加格式提示或写入 JIRA：

| 模型 / 网站记录 | 实际命令数 | 结果工具提交 | 可下载文件 | 内容验收 |
| --- | --- | --- | --- | --- |
| [GLM-5.3](http://10.2.118.76:8013/jira-agent?view=testing&case=jtc_48d02b86cfd1&tab=records&run=jtr_89b0a16f0750) | 13 | 1 次，直接接收 | 8 | 通过 |
| [deepseek-v4-flash](http://10.2.118.76:8013/jira-agent?view=testing&case=jtc_48d02b86cfd1&tab=records&run=jtr_df07dcbce865) | 11 | 2 次；首次多填 assistance 被业务校验拒绝，自行改正后接收 | 6 | 未全部通过：随机值已写入报告和评论草稿，但未按任务要求写入结构化 evidence |

两者均复现 `AssertionError`，仅将 `a - b` 改为 `a + b`，原测试转为 `PASS`，提交 `fixed_pending_review`，文件收集完成且未触发补交恢复。14 个下载文件与网站记录、B 端 SHA-256 一致，原材料和测试未变，下载源码独立复跑均通过。浏览器无脚本错误，临时账号已清理。GLM 的目录错误及缺少 `patch` 命令保留在记录中；DeepSeek 的证据遗漏仍记为验收失败。以上为人工程序核验，未启用模型自动评分。

初版专项 35 项和相关回归 200 项分批通过，其中两项过时的目录断言修正后重跑。后续仅合并重复校验用例、移除内存中的格式副本；格式直接读取现有线程记录，仍覆盖完整还原、非法值拒绝、新旧线程续办、重启/断线恢复、重复提交、取消和冻结 Schema。8011、8015 及共享 Codex 后端未因本次改动重启。

## 根因线索与修复位置

最符合当前现象的解释是：服务端的 JSON 输出约束或输出解析与工具调用冲突。例如，如果推理引擎把“只能生成最终 JSON”应用到整段生成过程，就可能阻止工具调用标记。这是**待服务端验证的假设**。

上游有相似问题可供维护方核对：

- [vLLM #39929](https://github.com/vllm-project/vllm/issues/39929) 记录了原生输出格式阻止自动工具调用的现象。[修复 PR #56086](https://github.com/vllm-project/vllm/pull/56086/files) 于 2026-09-27 合并，将受支持的工具调用语法与最终 JSON 语法组合。其测试也明确存在工具解析器、严格工具及 Schema 支持范围的限制，不能保证仅升级版本就适用于当前模型。
- [SGLang #42269](https://github.com/sgl-project/sglang/issues/42269) 报告了 GLM-5.3 配合工具与输出格式时跳过工具的问题，并讨论了组合工具语法与最终 JSON 语法的处理方式。该报告与本次症状相似，但不能据此认定 MetaX 使用 SGLang。

需要模型服务维护方用相同探针做网关与上游直连对照，保留模型版本、工具定义、原生格式约束及推理参数：

| 对照结果 | 下一步修复 |
| --- | --- |
| 上游直连通过，经过网关失败 | 检查网关实际转发的 `tools`、`tool_choice`、`strict`、`response_format` / `text.format`，以及响应中的工具调用是否被错误转为文本；修正对应路由或转换代码。 |
| 上游直连也失败 | 检查推理引擎版本、工具解析器、推理解析器与结构化解码实现；按实际引擎修复或升级，再验证当前模型。 |
| GLM 纯 Schema 控制仍返回非法 JSON | 同时检查结构化解码与推理/文本边界解析；不能仅以“已经能调用工具”验收。 |

正确修复应允许模型先调用工具、收到结果后再输出符合 Schema 的结论。不能通过丢弃 Schema、强制每一轮调用工具，或仅把工具语法当作普通文本返回来宣称支持。

## 当前状态与验收

8013 实际对话另发现一项独立的网络配置问题：4511 的 Codex 进程设置了公司代理，但 `NO_PROXY` / `no_proxy` 缺少 MetaX 域名。同一容器和同一 Key 下，沿用进程代理访问 `/models`、`/responses` 均返回 HTML 403，直连均返回 200。该 403 与原生 Schema 问题独立；先前强制直连的隔离测试没有覆盖这条实际访问路径。部署时须按 [代理配置说明](jira-agent.md#metax-自建模型网关) 校验实际进程环境。

现已给 4510、4511 的两个环境变量追加 MetaX 域名，并在确认没有活动 Codex 任务后重建对应容器，保留原有运行文件、挂载和认证。两个实际进程环境下的 `/models`、`/responses` 均返回 200；通过各自真实 WebSocket app-server 调用 GLM-5.3 的简短对话均完成并返回 `NETWORK_OK`。此检查不带原生 Schema，不代表结构化输出兼容问题已解决。

2026-11-13 对照现有 OpenCode 1.18.30 配置，将两个 MetaX 模型的档位同步为 `low / high / max`，默认 `max`。配置映射见 [部署说明](jira-agent.md#metax-自建模型网关)。本次验证结果：

- 真实 Codex 0.162.1 在隔离 HTTP 接收端发出了 `reasoning.effort = "max"`，模型列表接受三个档位；没有添加 OpenCode SDK 专用字段。
- 两个模型分别以三个档位调用真实 Responses 接口，不带原生输出约束的六组请求均调用了 `read_probe`。五组最终 JSON 的随机值校验通过；GLM 的 `low` 返回了 Markdown JSON 代码块，未通过探针的严格 JSON 解析，不能将六组全部记为结构化输出通过。
- 当时已更新 4510 使用的 `.codex/metax-models.json`；8013 的模型目录读取和选择校验通过，两个模型默认均为 `max`，默认服务商与模型未变。模型选择相关的五项回归通过。**后续更正**：这里只验证了选择列表；执行端能力变更仍需在启动时加载合并目录并重启，见上文实际任务排查。

本次没有取得网关或推理引擎的部署位置及配置，因此尚未应用、验证服务端修复。网站对 MetaX 的 JIRA 执行改用结果工具，其他 provider 保持原生 `outputSchema`。旧 MetaX 对话不能补注册动态工具，需新建对话；历史记录和文件保留。实际任务验证结果另行记录，不以隔离探针代替实际任务验收。

服务端修复后，先要求探针的两个接口及纯 Schema 控制通过，再在 8013 的隔离任务中验证实际文件变化、原生结构化结论和同一线程续办，并检查流式结果。验证通过前，正式任务可继续使用已验证的 provider。
