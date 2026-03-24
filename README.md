# 炉石传说脚本

## 1. 项目概况

这是一个运行在 Windows 桌面前台环境中的炉石传说自动对战 Bot。
当前目标不是复杂策略，而是先把“可稳定跑局”的识别与决策主链路收敛清楚。

当前固定前提：
- 前台截图
- 前台输入
- 简体中文客户端
- 固定分辨率 profile：`1600x900` / `1440x900`
- 窗口位置固定：`(20, 20)`
- 休闲模式
- `deck_index=1-9`

## 2. 当前 battle 主链路

当前 battle 阶段已经完成从“绿色高亮可出牌判断”到“OCR / 费用宝石 + 规则判定”的主链路切换。

### 职责边界
- `bot/vision/board_state.py`
  - `parse_board_state()` **只负责 battle 基础状态**
  - 例如：是否我方回合、结束回合按钮是否可点、基础 battle 状态
  - 不再负责最终 `hand_cards` 决策产出
- `bot/runtime.py`
  - 负责将 OCR mana / cost 识别结果组装成最终决策使用的 `hand_cards`
  - 负责 OCR 可信度判定、规则校验、fallback 语义和运行时处置
- `bot/strategy/rules.py`
  - 只消费上游已经 finalized 的 `BoardState.hand_cards`
  - 不重新解释 legacy 绿色候选，也不额外发明隐式 fallback

### 当前最终手牌来源
battle 决策中使用的最终 `hand_cards` **仅来自 OCR / 费用宝石链路**。

旧的绿色高亮候选链路仍保留少量 debug 能力，但已经明确是：
- debug-only
- not used for decision
- 不是正式 fallback

## 3. 优先级 1-4 当前落实情况

### 优先级 1：统一手牌识别主链路
**状态：已完成主体，并已收尾去歧义。**

已落实：
- `parse_board_state()` 只返回 battle 基础状态
- 最终 `hand_cards` 仅在 runtime 中由 OCR / cost gem 链路生成
- `hand_source` / `hand_cards_ready` 已明确区分“可决策结果”和“等待 OCR”状态
- legacy 绿色候选已明确标注为 debug-only
- 日志已拆分“final cards”与“debug-only legacy candidates”，减少混链错觉

### 优先级 2：mana / cost 分离 OCR 预处理配置
**状态：已完成。**

已落实：
- `bot/ocr_runtime.py` 中 `recognize_mana()` / `recognize_cost()` 分别走各自配置
- `bot/ocr/1440x900.yaml`、`bot/ocr/1600x900.yaml` 已有独立的 `mana:` / `cost:` 配置段

### 优先级 3：费用宝石检测改为“候选 + 验证”两阶段
**状态：已完成第一版。**

已落实：
- cost gem 检测已不再是简单候选即采信
- runtime 中增加了候选验证指标，例如：
  - 蓝色区域占比
  - 白字区域占比
  - 圆形结构/填充稳定性
  - 边缘密度
  - 综合分数
- 目标是降低高密度手牌、特效干扰下的漏检与误检

### 优先级 4：OCR 拒识与规则校验机制
**状态：已完成第一版。**

已落实：
- `bot/ocr_runtime.py` 中为 mana / cost OCR 增加显式拒识机制：
  - `best_diff` 过大拒识
  - `best/second-best` 差距过小拒识
- `bot/runtime.py` 中增加规则校验：
  - `0 <= mana_current <= 10`
  - `0 <= mana_total <= 10`
  - `mana_current <= mana_total`
  - `0 <= cost <= 20`
- 增加轻量帧间跳变校验：
  - `mana_total` 单帧异常大跳变拒绝
  - `mana_current` 单帧异常大跳变拒绝
- `BoardState` 已增加 OCR 可信状态表达
- OCR 不可信时，策略层会等待，不会错误出牌
- battle 阶段已增加异常优先采样：当 mana / cost OCR 拒识、不可信、规则校验失败、`ocr_wait_mana` / `ocr_wait_cost`、`hand_cards_not_ready`、legacy debug candidates 与 final cards 明显不一致等情况出现时，会额外保存 `ocr_anomaly` 样本，并落结构化 `meta.json`
- `ocr_anomaly` 的 `meta.json` 现已统一基础字段：`sample_id`、ISO 风格 `timestamp` / `captured_at`、`timestamp_compact`、`profile`、`tag`、`window`
- 异常样本除主 `trigger_reason` 外，还会保留 `all_trigger_reasons`，便于后续标注和归类时看到更完整上下文
- 可用 `python -m bot.build_anomaly_manifest --resolution 1440x900 --tag ocr_anomaly` 生成轻量索引 CSV，快速查看 anomaly 样本关键信息
- 同一脚本现支持轻量过滤/汇总，例如：
  - 最近的 `ocr_untrusted`：`python -m bot.build_anomaly_manifest --resolution 1440x900 --tag ocr_anomaly --has-trigger-reason ocr_untrusted --limit 20`
  - `ocr_wait_cost` 样本：`python -m bot.build_anomaly_manifest --resolution 1440x900 --tag ocr_anomaly --trigger-reason ocr_wait_cost --limit 20`
  - `final_cards_count=0` 且 `debug_candidate_count>0`：`python -m bot.build_anomaly_manifest --resolution 1440x900 --tag ocr_anomaly --only-zero-final-with-debug`
  - 查看常见 reject / trigger reason：`python -m bot.build_anomaly_manifest --resolution 1440x900 --tag ocr_anomaly --stats --limit 50`

## 4. 当前已知限制与未完成项

以下仍是当前限制，不要在文档里假装已经完成：
- `cost OCR` 仍是模板匹配，不是通用 OCR
- `1440x900` 仍需要持续积累真实对局样本
- OCR 拒识阈值和规则阈值目前仍偏经验值，需要继续实战调参
- 攻击逻辑尚未实现
- 不支持动态分辨率、后台截图、后台输入

## 5. 关键文件

核心运行：
- `bot/runtime.py`
- `bot/strategy/rules.py`
- `bot/action/mouse.py`
- `bot/action/hotkey.py`

视觉识别：
- `bot/vision/scene.py`
- `bot/vision/board_state.py`
- `bot/vision/matcher.py`

OCR：
- `bot/ocr_runtime.py`
- `bot/ocr_labeler_app.py`
- `bot/ocr_label_main.py`
- `bot/ocr_label_mana_main.py`
- `bot/ocr_label_cost_main.py`

配置资源：
- `bot/regions/1600x900.yaml`
- `bot/regions/1440x900.yaml`
- `bot/templates/1600x900/templates.yaml`
- `bot/templates/1440x900/templates.yaml`
- `bot/ocr/1600x900.yaml`
- `bot/ocr/1440x900.yaml`

## 6. 启动方式

安装依赖：
```powershell
pip install -r requirements.txt
```

主程序：
```powershell
python -m bot.main --deck-index 1 --resolution 1600x900
python -m bot.main --deck-index 1 --resolution 1440x900
python -m bot.main --deck-index 1 --resolution 1440x900 --ocr-auto-sample
```

标注工具：
```powershell
python -m bot.ocr_label_mana_main
python -m bot.ocr_label_cost_main
```

停止：
```text
F8
```

## 7. 当前建议顺序

1. 持续补充 `1440x900` 实战样本
2. 持续补标 `mana_to_label.csv` / `cost_to_label.csv`
3. 用实战日志验证 priority 4 的拒识和规则校验是否过松/过严
4. 如有固定误识模式，再调整 OCR 预处理、拒识阈值或 gem 验证参数
5. OCR 主链路稳定后，再推进攻击逻辑
