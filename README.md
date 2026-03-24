# 炉石传说脚本

## 1. 项目概况

这是一个运行在 Windows 桌面前台环境中的炉石传说自动对战 Bot。
当前目标不是复杂策略，而是先把**稳定跑局 + 识别可回流优化**这两条线打通。

当前固定前提：
- 前台截图
- 前台输入
- 简体中文客户端
- 固定分辨率 profile：`1600x900` / `1440x900`
- 窗口位置固定：`(20, 20)`
- 休闲模式
- `deck_index=1-9`

## 2. 当前 battle 主链路

battle 阶段已经完成从“绿色高亮 playable 判断”到“OCR / 费用宝石 / 规则校验”的主链路切换。

### 职责边界
- `bot/vision/board_state.py`
  - `parse_board_state()` **只负责 battle 基础状态**
  - 不再负责最终 `hand_cards` 产出
- `bot/runtime.py`
  - 负责 OCR mana / cost 识别
  - 负责 cost gem 候选验证
  - 负责 OCR 拒识、规则校验、fallback 语义和异常采样
  - 负责把最终决策使用的 `hand_cards` 组装出来
- `bot/strategy/rules.py`
  - 只消费 finalized `BoardState`
  - 不重新解释 legacy 绿色候选
  - 不额外发明隐式 fallback

### 当前最终手牌来源
battle 决策中使用的最终 `hand_cards` **仅来自 OCR / 费用宝石链路**。

旧的绿色高亮候选链路仍保留少量 debug 能力，但已经明确是：
- debug-only
- not used for decision
- 不是正式 fallback

## 3. 优先级 1-5 当前落实情况

### 优先级 1：统一手牌识别主链路
**状态：已完成。**

已落实：
- `parse_board_state()` 只返回 battle 基础状态
- 最终 `hand_cards` 仅在 runtime 中由 OCR / cost gem 链路生成
- `hand_source` / `hand_cards_ready` 语义已收口
- legacy 绿色候选已明确标为 debug-only
- 日志已区分 final decision state 与 debug-only candidates

### 优先级 2：mana / cost 分离 OCR 预处理配置
**状态：已完成。**

已落实：
- `recognize_mana()` / `recognize_cost()` 分别使用各自配置
- `bot/ocr/1440x900.yaml`、`bot/ocr/1600x900.yaml` 已拆分 `mana:` / `cost:`

### 优先级 3：费用宝石检测改为“候选 + 验证”两阶段
**状态：已完成第一版。**

已落实：
- gem 检测不再是简单候选即采信
- 已引入蓝色占比、白字占比、结构稳定性、综合分数等验证指标
- 目标是降低高密度手牌和特效场景下的漏检 / 误检

### 优先级 4：OCR 拒识与规则校验机制
**状态：已完成第一版。**

已落实：
- mana / cost OCR 增加显式拒识：
  - `best_diff` 过大拒识
  - `best/second-best` 差距过小拒识
- runtime 增加规则校验：
  - `0 <= mana_current <= 10`
  - `0 <= mana_total <= 10`
  - `mana_current <= mana_total`
  - `0 <= cost <= 20`
- 已增加轻量帧间跳变校验
- OCR 不可信时，策略层不会继续出牌

### 优先级 5：样本标注闭环
**状态：已推进到 P5-3 轻量版。**

#### P5-1：异常样本采样闭环
已落实：
- battle 阶段增加 anomaly 优先采样
- anomaly 样本单独目录：`bot/samples/<profile>/ocr_anomaly/`
- 新增结构化 `meta.json`
- 日志可打印 `sample_id` / `trigger_reason`

#### P5-2：anomaly 元信息规范化与索引
已落实：
- `meta.json` 基础字段进一步统一
- 保留 `trigger_reason`，并新增 `all_trigger_reasons`
- 新增索引脚本：`python -m bot.build_anomaly_manifest`
- 可生成 `anomaly_index.csv`

#### P5-3：anomaly 过滤 / 汇总能力
已落实：
- `bot.build_anomaly_manifest` 支持按条件过滤 anomaly 样本
- 支持轻量统计常见 trigger / reject reason
- 已能快速回答：
  - 最近有哪些 `ocr_untrusted` 样本
  - 哪些样本是 `ocr_wait_cost`
  - 哪些样本 `final_cards=0` 且 `debug_candidate_count>0`
  - 哪些 reject reason 最常见

## 4. 当前可用的 anomaly 工具

### 生成 anomaly 索引
```powershell
python -m bot.build_anomaly_manifest --resolution 1440x900 --tag ocr_anomaly
```

### 查询包含 `ocr_untrusted` 的样本
```powershell
python -m bot.build_anomaly_manifest --resolution 1440x900 --tag ocr_anomaly --has-trigger-reason ocr_untrusted
```

### 查询主原因是 `ocr_wait_cost` 的样本
```powershell
python -m bot.build_anomaly_manifest --resolution 1440x900 --tag ocr_anomaly --trigger-reason ocr_wait_cost
```

### 查询 `final_cards=0` 且 `debug_candidate_count>0` 的样本
```powershell
python -m bot.build_anomaly_manifest --resolution 1440x900 --tag ocr_anomaly --only-zero-final-with-debug
```

### 查看常见 trigger / reject reason 统计
```powershell
python -m bot.build_anomaly_manifest --resolution 1440x900 --tag ocr_anomaly --stats
```

## 5. 当前已知限制与未完成项

仍需明确承认的限制：
- `cost OCR` 仍是模板匹配，不是通用 OCR
- `1440x900` 仍需要持续积累真实对局样本
- OCR 拒识阈值、规则阈值、gem 验证阈值仍需继续实战调参
- anomaly 索引和过滤工具仍是轻量 CLI，不是完整分析平台
- 攻击逻辑尚未实现
- 不支持动态分辨率、后台截图、后台输入

## 6. 关键文件

核心运行：
- `bot/runtime.py`
- `bot/strategy/rules.py`
- `bot/action/mouse.py`
- `bot/action/hotkey.py`

视觉识别：
- `bot/vision/scene.py`
- `bot/vision/board_state.py`
- `bot/vision/matcher.py`

OCR 与样本闭环：
- `bot/ocr_runtime.py`
- `bot/sampler.py`
- `bot/build_anomaly_manifest.py`
- `bot/ocr_labeler_app.py`
- `bot/ocr_label_mana_main.py`
- `bot/ocr_label_cost_main.py`

配置资源：
- `bot/regions/1600x900.yaml`
- `bot/regions/1440x900.yaml`
- `bot/templates/1600x900/templates.yaml`
- `bot/templates/1440x900/templates.yaml`
- `bot/ocr/1600x900.yaml`
- `bot/ocr/1440x900.yaml`

## 7. 启动方式

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

## 8. 当前建议顺序

1. 先实际跑局，产出 anomaly 样本
2. 用 `bot.build_anomaly_manifest` 过滤 / 汇总问题样本
3. 针对高频 trigger / reject reason 回看样本与日志
4. 继续补标和调阈值
5. OCR 主链路稳定后，再推进攻击逻辑
