# Model 注册指南

> 这份文档**不是**SKILL.md的一部分,不会在"分析图片"这类正常调用skill的场景下被自动加载——
> SKILL.md只在skill被触发时(即真的要分析一张图)才进上下文,而"给config加一个新model"是一次
> **配置维护任务**,不是一次分析任务,skill机制不会主动把这份文档递给你。
>
> 换句话说：**这份文档是给正在编辑`config/vision-config.json`的人（或者被要求做这件事的agent）看的**，
> 你需要主动打开它，不会有人自动帮你想起来读它。

## 谁来注册一个新model？

现状：**完全靠人工**。没有自动发现机制、没有"扫描一遍网络上有哪些新model自动加进来"这种东西。
注册一个model就是打开`config/vision-config.json`，找到对应`providers`下的provider（没有就新建一个），
往它的`models`数组里加一段JSON，存盘。可以是你自己手动改，也可以让某个agent（比如Claude Code）帮你改——
但不管谁改，最终都是同一个动作：编辑这个文件。

## 一个新model该配哪些字段？

**config按provider分组**，同一provider下的model共享`base_url`/`api_format`/`api_key_env`，只写一次：

```json
"providers": {
  "你的provider名（如openai/google/alibaba/self-hosted，自定义即可）": {
    "base_url": "API端点",
    "api_format": "anthropic | openai | google | omniparser（决定走哪个adapter，见下）",
    "api_key_env": "环境变量名，没有key就填null",
    "models": [
      {
        "name": "model名字，同一provider下不能重复；不同provider下允许同名（身份识别用完整的provider/name）",
        "alias": "可选，全局唯一的短名字，不能含'/'，--model/--verify-grounding/preferred_models都认",
        "capabilities": ["..."],
        "coordinate_convention": "仅grounding/ui-grounding类model需要，见下",
        "priority": 1,
        "timeout": 60,
        "max_tokens": 4096,
        "rpm_limit": 60
      }
    ]
  }
}
```

**身份识别用`provider/name`复合ref，不是裸`name`**——`name`只需要在同一provider下不重复（加载时校验，
重复直接拒绝），不同provider下完全可以同名。`--model`/`--verify-grounding`/`preferred_models`这些地方
引用model时可以写完整的`provider/name`（如`google/gemini-3-pro`），也可以写`alias`（如果配了的话），
只有在候选池里这个裸name唯一时才允许简写成裸name，跨provider重名时必须写全称或alias，否则报错拒绝而
不是随便选一个。`provider`只用于`capability_provider_whitelist`校验，不单独参与身份识别（要跟`name`
拼在一起才是完整身份）。

model条目里也可以覆盖同provider的共享字段（比如某个model走不同的`base_url`），字段同名时model
条目优先——但正常情况下不需要这么做，同provider的model共享同一套连接参数是默认假设。

## `models`是资源清单，"该用哪个"挂在role底下

`providers.<id>.models`只负责回答"有哪些model、各自能干什么"，不承载任何"这次该选谁"的逻辑——那是
role自己的事。role想要一份"快捷候选子集"，直接在role定义里写`preferred_models`：

```json
"roles": {
  "comprehensive": {
    "preferred_models": ["sonnet", "gpt"],
    ...
  }
}
```

`preferred_models`里既可以写`alias`（上面例子的`sonnet`/`gpt`），也可以写完整`provider/name`——不需要
一个独立于role之外的顶层结构，这是最初设计`scenarios`时走的弯路：`scenarios`需要一个额外的
`--scenario NAME`命令行参数才能触发，而vision-engine大多数调用场景里根本没有一个天然存在的角色会去
决定"这次该传哪个scenario名字"，导致这层配置写了也没人用。已移除，别再抄这个模式。

`api_format`只能是这四个值之一，因为目前只实现了这四个adapter（`scripts/adapters/`下对应的
`anthropic_api.py`/`openai_api.py`/`google_api.py`/`omniparser_api.py`）。如果你的model走的是
一套完全不同的私有协议，光改json解决不了，需要新写一个adapter文件并在`vision-analyze.py`的
`ADAPTER_BY_FORMAT`字典里注册——这是唯一必须碰代码的情况。绝大多数情况（包括几乎所有自建的
OpenAI兼容服务，如vLLM/Ollama/LM Studio）直接填`"openai"`就能用，不需要写代码。

## capabilities 注册表 —— 到底该打哪些标签

只有4个capability会真正影响路由（被某个role的`requires`用到）：

| capability | 含义 | 判断依据 |
|---|---|---|
| `general` | 能看图并做基础描述/分析 | 能接收图片输入、给出有意义描述，就该打——门槛最低，多数视觉模型都有 |
| `ocr` | 文字提取**准确率**可靠 | 不是"能看图就行"，要求对密集文本/小字号/表格的识别准确率过关。没实测过准确率，先别打，宁可只给`general` |
| `grounding` | 经专门训练、给出的像素级bbox坐标可信 | **门槛最高，最容易配错**。判断依据不是"model嘴上说自己能给坐标"（通用对话模型问了也会给，但是编的），而是provider官方文档/论文明确写了做过grounding/detection训练。**拿不准，用下面的`--verify-grounding`实测，别只信文档** |
| `ui-grounding` | 专门的UI元素检测服务 | 只有OmniParser这类专用检测服务该打，通用视觉模型不该打，语义上是"只做UI元素枚举、不接受自然语言查询" |

**不影响路由的标签**（当前config里已清空，除非你先在`roles`里新增一个真正`requires`它的role，否则打了也是摆设）：
`data`、`style`、`spatial-relative`。

**加了新model或改了capabilities，CLI下次启动会自动做两项静态校验**（不阻断，只警告，见stderr）：
- 死标签：某model声明了某capability，但没有role要求它
- 不可满足：某role要求了某capability，但没有model声明它，这个role会永远选不到候选

**校验能发现"标签有没有被用到"，发现不了"标签打得准不准"**——这是下一节要解决的问题。

## `grounding`标签该不该打——别只信文档，用实测

之前唯一的判断依据是"相信provider文档"，太弱。现在有个内置的实测工具：

```bash
scripts/vision-analyze.py --verify-grounding MODEL_NAME --config config/vision-config.json
```

它会：
1. 用内置探测图（`scripts/fixtures/grounding-probe.png`，一张1000x1000白底图，中央有一个位置已知的红色矩形）
2. 直接调用你指定的model，问它"找出红色矩形"
3. 把返回的bbox和ground truth（`scripts/fixtures/grounding-probe-truth.json`）计算IoU（交并比）
4. IoU≥0.5 判定"有实测证据支持打grounding标签"，否则给出"不建议打"的结论

输出示例：
```json
{
  "status": "completed",
  "model": "gemini-3-pro",
  "best_iou": 0.87,
  "recommendation": "IoU=0.87（阈值0.5），达标。有实测证据支持给'gemini-3-pro'打grounding标签。"
}
```

**这个工具的能力边界，别过度信任**：
- 单张探测图上表现好，不代表真实复杂场景（遮挡、密集元素、非规则形状）也一样准，正式启用前建议再用真实场景图人工抽查几次。
- IoU阈值0.5是经验值，不是理论最优——如果你的场景对精度要求特别高（比如自动化点击必须命中），可以把判断标准提高，自己核对`best_iou`的具体数值而不是只看`recommendation`这一句话结论。
- 这个工具验证的是"这个model至少不是在瞎编坐标"，不是"这个model在所有任务上都够用"。

## UI检测类model该配`grounding`还是`ui-grounding`——按交互模式判断，不是按"能不能看UI"

这条是2026年7月接入UI-TARS时才发现的坑，记下来避免以后重复踩：`grounding`和`ui-grounding`的区分标准不是"这个model擅不擅长UI"，而是**它的交互接口是"targeted"还是"enumerate"**：

- **targeted**（给一段描述，返回对应元素坐标）——配`grounding`，走`locate` role。UI-TARS属于这类，即使它专门训练来做UI任务，接口形态还是"回答一个查询"，跟Gemini/Qwen-VL的grounding调用方式是同一套。
- **enumerate**（不接受查询，一次性吐出画面里全部元素）——配`ui-grounding`，走`locate-ui` role。OmniParser属于这类。

配错的后果：如果把一个targeted接口的model硬配成`ui-grounding`，`locate-ui`的`enumerate_then_filter`逻辑会拿到一个不符合预期的响应格式（它期待"全部元素列表"，实际收到的是"一个查询的回答"），本地过滤逻辑会失效或者报错，而不是给出合理提示。加新model前先看它的API文档到底是"问答式"还是"全量扫描式"，再决定配哪个能力标签。

## role 字段完整说明

| 字段 | 含义 |
|---|---|
| `system_prompt` | 直接写在config里的短prompt(inline) |
| `system_prompt_file` | 指向`.md`文件的相对路径（相对config.json所在目录），加载时读入`system_prompt`，**不能跟`system_prompt`同时填**，两者都配会报错拒绝 |
| `requires` | 该role需要model具备的`capabilities`，驱动动态候选池筛选 |
| `output_schema` | `text`或`bbox_list`，决定走文本流程还是bbox容错提取+校验流程 |
| `default` | 标记默认role（不指定`-r`时用它） |
| `multi_image` | 是否需要两张图（`compare`用，要求必须传`-i2`） |
| `query_mode` | `targeted`（给描述找元素）或`enumerate_then_filter`（枚举全部再本地过滤），决定`-p`的语义 |
| `skip_context` | 是否忽略`-c`参数（`quick`用） |
| `preferred_models` | 该role的定向model偏好（软性排序，可写`provider/name`或`alias`） |

**长prompt建议外置成文件**：inline字符串编辑麻烦（转义换行、没高亮、git diff一坨），超过几行就该拆成
`config/prompts/<role>.md`，config里改成：
```json
{"system_prompt": null, "system_prompt_file": "prompts/comprehensive.md"}
```
`comprehensive`和`locate`两个role已经是这么做的，可以照着抄。

## 小结：新model注册的完整流程



1. 确认`api_format`是否已有对应adapter（四选一，或者自己写一个新的）
2. 先只打`general`（如果它能看图），别急着打`grounding`
3. 如果确实需要grounding能力，先跑`--verify-grounding`拿实测IoU再决定要不要打标签
4. 编辑`vision-config.json`加进对应provider的`models`数组（没有这个provider就新建一个），存盘即生效，不需要重启/编译任何东西
5. 跑一次任意role确认没有触发死标签/不可满足/重名警告（stderr里看）
