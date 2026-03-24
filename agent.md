# Hearthstone Bot Agent Notes

## 1. 项目现状

这是一个 Windows 前台自动化炉石 Bot。
当前代码主线已经明确：**battle 决策以 OCR / cost gem 为唯一正式手牌来源**，不再以绿色高亮 playable 作为最终主链路。

请把这份文档当作“当前真实状态说明”，而不是历史开发日记。
更新文档时优先保证和 `README.md`、`任务清单.md` 一致，避免割裂。

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

## 3. 关键代码职责边界

### `bot/vision/board_state.py`
- `parse_board_state()` 只负责 battle 基础状态
- 不负责最终 `hand_cards` 产出
- 旧绿色候选链路仅可作为 debug-only inspection 能力保留

### `bot/runtime.py`
- 负责 battle 阶段 OCR mana / cost 识别
- 负责 cost gem 候选验证
- 负责把 OCR 结果组装为最终 `hand_cards`
- 负责 OCR 拒识、规则校验、fallback 语义与运行时处置

### `bot/strategy/rules.py`
- 只消费 finalized `BoardState`
- 不重新解释 legacy debug candidates
- 不再引入额外隐式 fallback

## 4. 优先级 1-4 当前结论

### P1 统一手牌识别主链路
已完成。
- battle 决策使用的最终 `hand_cards` 来源唯一
- legacy 绿色候选已降级为 debug-only
- 日志表达已尽量避免“debug 候选”和“最终决策 cards”混淆

### P2 mana / cost 分离 OCR 配置
已完成。
- `recognize_mana()` / `recognize_cost()` 分别使用各自配置
- profile YAML 已拆分 `mana:` / `cost:` 配置段

### P3 cost gem 检测增强
已完成第一版。
- 候选 + 验证 两阶段已经落地
- 目标是降低高密度手牌和特效场景下的漏检/误检

### P4 OCR 拒识与规则校验
已完成第一版。
- 模板匹配已支持显式拒识
- runtime 已支持 mana/cost 合理范围校验
- 已支持轻量帧间异常跳变校验
- OCR 不可信时不会继续驱动出牌
- battle 异常场景会优先进入 `ocr_anomaly` 采样目录，并写入结构化 `meta.json`，用于后续标注和问题回溯

## 5. 当前仍需牢记的风险

- `cost OCR` 仍是模板匹配路线，泛化能力依赖样本覆盖
- 阈值仍需靠真实对局持续调参
- `1440x900` 仍应继续作为主验证 profile
- 攻击逻辑尚未实现

## 6. 修改代码时的原则

- 不要重新引入旧绿色 playable 作为最终 battle hand 决策来源
- 可以保留 legacy 手段做 debug，但必须明确写成 debug-only
- 修改日志时优先让“最终决策状态”和“debug 观察信息”分开
- 如果改 `runtime.py`，优先把 OCR 可信性与 fallback 语义收敛到统一入口
- 如果改 `board_state.py`，优先保持 battle base state 纯净
- 如果改 OCR，优先补样本，其次再调模板匹配和阈值
- OCR CSV 中优先使用仓库相对路径，避免新数据继续制造迁移负担

## 7. 推荐下一步

1. 继续积累 `1440x900` 实战样本
2. 持续补标 mana / cost 数据集
3. 观察 priority 4 拒识是否过于保守或过于宽松
4. OCR 链路稳定后，再推进攻击逻辑
