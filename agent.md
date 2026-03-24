# Hearthstone Bot Agent Notes

## 1. 当前项目状态

这是一个 Windows 前台自动化炉石 Bot。
当前代码主线已经明确：

- battle 决策以 **OCR / cost gem** 为唯一正式手牌来源
- legacy 绿色高亮链路仅保留 debug-only 价值
- OCR 已有第一版拒识、规则校验与异常样本回流闭环

更新文档时，优先保证与 `README.md`、`任务清单.md` 一致，不要让 README 说一套、agent 说一套、代码又是另一套。

## 2. 当前固定约束

- 仅支持 Windows
- 仅支持前台截图和前台输入
- 仅支持简体中文客户端
- 仅支持 `1600x900` / `1440x900`
- 窗口位置固定：`(20, 20)`
- 目标模式固定为休闲模式
- 套牌固定为 `deck_index=1-9`

默认不扩展到：
- 动态分辨率适配
- 后台截图
- 后台输入
- 中文卡名 OCR

## 3. 关键职责边界

### `bot/vision/board_state.py`
- `parse_board_state()` 只负责 battle 基础状态
- 不负责最终 `hand_cards` 产出
- 旧绿色候选链路仅可作为 debug-only inspection 能力保留

### `bot/runtime.py`
- 负责 battle 阶段 OCR mana / cost 识别
- 负责 cost gem 候选验证
- 负责把 OCR 结果组装为最终 `hand_cards`
- 负责 OCR 拒识、规则校验、fallback 语义与运行时处置
- 负责 anomaly 样本触发与落盘

### `bot/strategy/rules.py`
- 只消费 finalized `BoardState`
- 不重新解释 legacy debug candidates
- 不引入额外隐式 fallback

### `bot/sampler.py`
- 负责普通样本与 anomaly 样本的统一落盘
- 当前已支持 `meta.txt + meta.json`
- anomaly 样本依赖结构化 `meta.json` 供后续索引和过滤脚本消费

### `bot/build_anomaly_manifest.py`
- 负责 anomaly 索引构建
- 负责 anomaly 样本的轻量过滤与汇总
- 是当前 P5 阶段的主消费入口，不是大而全分析平台

## 4. 优先级 1-5 当前结论

### P1 统一手牌识别主链路
已完成。
- battle 决策使用的最终 `hand_cards` 来源唯一
- legacy 绿色候选已降级为 debug-only
- runtime / rules / board_state 职责边界已收口

### P2 mana / cost 分离 OCR 配置
已完成。
- `recognize_mana()` / `recognize_cost()` 分别使用各自配置
- profile YAML 已拆分 `mana:` / `cost:` 配置段

### P3 cost gem 检测增强
已完成第一版。
- 已采用候选 + 验证两阶段
- 目标是降低高密度手牌和特效干扰下的漏检 / 误检

### P4 OCR 拒识与规则校验
已完成第一版。
- 模板匹配已支持显式拒识
- runtime 已支持 mana/cost 合理范围校验
- 已支持轻量帧间异常跳变校验
- OCR 不可信时不会继续驱动出牌

### P5 样本标注闭环
已推进到 P5-3。
- P5-1：异常优先采样 + anomaly 目录 + `meta.json`
- P5-2：元信息规范化 + `all_trigger_reasons` + anomaly index
- P5-3：基于 `build_anomaly_manifest` 的过滤 / 汇总 CLI

## 5. 当前建议维护原则

- 不要重新引入旧绿色 playable 作为最终 battle hand 决策来源
- 可以保留 legacy 手段做 debug，但必须明确写成 debug-only
- 修改日志时优先让“最终决策状态”和“debug 观察信息”分开
- 改 OCR 逻辑时，优先考虑：
  1. 样本是否足够
  2. anomaly 是否已被采集到
  3. reject / trigger reason 是否能被复盘
- 不要把 P5 工具误做成复杂平台；当前目标是“小而实用、马上能排障”

## 6. 当前仍需牢记的风险

- `cost OCR` 仍是模板匹配路线，泛化依赖样本覆盖
- 阈值仍需靠真实对局持续调参
- `1440x900` 仍应继续作为主验证 profile
- 当前 anomaly 工具偏离线，仍不是完整统计系统
- 攻击逻辑尚未实现

## 7. 推荐下一步

1. 先跑实战，积累 anomaly 样本
2. 用 `bot.build_anomaly_manifest` 查高频 trigger / reject reason
3. 定向补标并回看对应样本
4. 再决定是继续推进 P5-4，还是回头调 OCR / gem 阈值
