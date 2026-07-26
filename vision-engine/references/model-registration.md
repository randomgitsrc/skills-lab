# Model 注册指南

> 这份文档**不是**SKILL.md的一部分,不会在"分析图片"这类正常调用skill的场景下被自动加载——
> SKILL.md只在skill被触发时(即真的要分析一张图)才进上下文,而"给config加一个新model"是一次
> **配置维护任务**,不是一次分析任务,skill机制不会主动把这份文档递给你。
>
> 换句话说：**这份文档是给正在编辑`config/vision-config.json`的人（或者被要求做这件事的agent）看的**，
> 你需要主动打开它，不会有人自动帮你想起来读它。

## 谁来注册一个新model？

现状：**完全靠人工**。没有自动发现机制、没有"扫描一遍网络上有哪些新model自动加进来"这种东西。
注册一个model就是打开`config/vision-config.json`，往`models`数组里加一段JSON，存盘。可以是你自己手动改，
也可以让某个agent（比如Claude Code）帮你改——但不管谁改，最终都是同一个动作：编辑这个文件。

## 一个新model该配哪些字段？

```json
{
  "name": "你起的名字",
  "provider": "标识这是谁家的（如openai/google/alibaba/self-hosted）",
  "base_url": "API端点",
  "api_format": "anthropic | openai | google | omniparser（决定走哪个adapter，见下）",
  "api_key_env": "环境变量名，没有key就填null",
  "capabilities": ["..."],
  "coordinate_convention": "仅grounding/ui-grounding类model需要，见下",
  "priority": 1,
  "timeout": 60,
  "max_tokens": 4096,
  "rpm_limit": 60
}
```

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
  "model": "gemini-2.5-flash",
  "best_iou": 0.87,
  "recommendation": "IoU=0.87（阈值0.5），达标。有实测证据支持给'gemini-2.5-flash'打grounding标签。"
}
```

**这个工具的能力边界，别过度信任**：
- 单张探测图上表现好，不代表真实复杂场景（遮挡、密集元素、非规则形状）也一样准，正式启用前建议再用真实场景图人工抽查几次。
- IoU阈值0.5是经验值，不是理论最优——如果你的场景对精度要求特别高（比如自动化点击必须命中），可以把判断标准提高，自己核对`best_iou`的具体数值而不是只看`recommendation`这一句话结论。
- 这个工具验证的是"这个model至少不是在瞎编坐标"，不是"这个model在所有任务上都够用"。

## 小结：新model注册的完整流程

1. 确认`api_format`是否已有对应adapter（四选一，或者自己写一个新的）
2. 先只打`general`（如果它能看图），别急着打`grounding`
3. 如果确实需要grounding能力，先跑`--verify-grounding`拿实测IoU再决定要不要打标签
4. 编辑`vision-config.json`加进`models`数组，存盘即生效，不需要重启/编译任何东西
5. 跑一次任意role确认没有触发死标签/不可满足警告（stderr里看）
