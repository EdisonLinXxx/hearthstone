# Hearthstone Bot Agent Notes

## 1. 项目概况

这是一个运行在 Windows 桌面前台环境中的炉石传说自动对战 Bot。当前目标不是复杂策略，而是稳定跑通以下链路：

- 进入游戏
- 进入对战
- 选择套牌
- 开始匹配
- 处理匹配失败弹窗
- 起手确认
- 基础出牌
- 结束回合
- 处理结算页
- 回到队列继续

## 2. 当前固定约束

- 仅支持 Windows
- 仅支持前台截图和前台输入
- 仅支持简体中文客户端
- 仅支持 `1600x900` / `1440x900`
- 窗口位置固定为 `(20, 20)`
- 目标模式固定为休闲模式
- 套牌选择固定为 `deck_index=1-9`

默认不扩展到：

- 动态分辨率适配
- 后台截图
- 后台输入
- 中文卡名 OCR

## 3. 关键文件

核心运行：

- `bot/runtime.py`
- `bot/strategy/rules.py`
- `bot/action/mouse.py`
- `bot/action/hotkey.py`

视觉识别：

- `bot/vision/scene.py`
- `bot/vision/board_state.py`
- `bot/vision/matcher.py`

OCR 相关：

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

## 4. 当前场景模型

静态模板场景：

- `startup`
- `main_menu`
- `battle_menu`
- `queue_page`
- `matching`
- `mulligan`
- `battle`
- `result_continue`
- `confirm_dialog`
- `unknown`

运行时归一化后使用的业务场景：

- `match_error`
- `result`
- `result_continue`
- `battle`

注意：

- `confirm_dialog` 是中性场景，不要直接在 `scene.py` 里写死成 `result`
- `match_error` / `result` 需要结合 `runtime.py` 中的上下文归一化

## 5. 本轮关键变更

### OCR 标注工具拆分

- 原 `bot.ocr_label_main` 已拆分为两个独立工具：
  - `python -m bot.ocr_label_cost_main`
  - `python -m bot.ocr_label_mana_main`
- 公共 Tk 标注界面抽到 `bot/ocr_labeler_app.py`
- `bot/ocr_label_main.py` 保留兼容入口
- `cost` 模式界面已改成单图优先，不再显示 full 上下文
- `mana` 模式保留完整界面上下文，便于标当前法力值/总法力值

### OCR 自动采样链路重做

- `mana` 区域已改为右下角当前法力值/总法力值区域
- `hand` 区域已改为整排手牌的最大边界
- 自动采样当前保存：
  - `full`
  - `mana`
  - `hand`
  - `cost_card_*`
  - `meta`
- `cost_card_*` 不再依赖旧的绿色高亮可出牌检测，而是直接在 `hand` 区域中找费用宝石后裁图

### 单卡费用切图优化

- 先在 `hand` 区域中按蓝色费用宝石做候选筛选
- 再做圆形检测和裁图校验
- 当前有效过滤规则包括：
  - 最小尺寸约束
  - 最大尺寸约束
  - 蓝色区域占比
  - 白色数字占比
  - 灰度对比度
- 最近一轮人工标注结果：
  - `mana`: `9/9 done`
  - `cost`: `15 done / 1 skip`
- 当前判断：`mana` 采样链路已稳定，`cost` 采样链路已达到可用状态

### 纯 OCR 出牌方案接入

- 新增 `bot/ocr_runtime.py`
- 运行时直接加载：
  - `bot/datasets/ocr/<profile>/mana_to_label.csv`
  - `bot/datasets/ocr/<profile>/cost_to_label.csv`
- 只使用 `label_status=done` 且 `label` 非空的样本
- 识别链路：
  - OCR 识别当前法力/总法力
  - OCR 识别每张手牌费用
  - 用 `mana_cost <= mana_current` 判定是否可出牌
- 已从 battle 决策路径中剥离旧的绿色高亮 `playable` 判定
- 当前 battle 决策路径是纯 OCR：
  - 检测手牌位置
  - OCR 识别法力
  - OCR 识别费用
  - 按 OCR 费用判断可出牌
  - 决策出牌

## 6. 当前已知风险

- `1440x900` 仍需持续补充真实对局样本
- `cost` OCR 当前是模板匹配方案，不是通用 OCR，识别范围受已标样本覆盖度影响
- 后续若出现新的数字样式、遮挡或特效，仍需继续补标
- 攻击逻辑尚未实现

## 7. 修改代码时的原则

- 不要重新引入固定像素点击偏移
- 不要重新引入 `match_count`
- 点击/拖拽后保留鼠标停靠逻辑，避免 hover 干扰
- 如果改 `runtime.py`，优先把判断收敛到统一入口，不要散落多个兜底分支
- 如果改 `board_state.py`，优先降低误检，再考虑提高召回
- 如果改 OCR 识别，优先补数据集，再考虑调整模板匹配和预处理
- OCR 数据集 CSV 中的 `image_path` / `full_path` / `meta_path` 必须优先写相对仓库根目录的相对路径，不要默认写绝对路径
- 运行时需要兼容旧的绝对路径 CSV，但后续新生成或重写的 CSV 一律使用相对路径，便于跨主机和虚拟机复用
- OCR 标注 CSV 重建必须采用增量合并，不允许默认全量清空已有 `label` / `label_status`
- 增量合并时应按 `sample_id` 保留已有 `done` / `skip`，只为新样本补空白行
- 只有当样本文件本身被删除时，重建 CSV 才允许移除对应记录

## 8. 当前建议顺序

1. 继续积累 `1440x900` 实战样本
2. 持续补标 `mana_to_label.csv` 和 `cost_to_label.csv`
3. 观察纯 OCR 出牌在真实对局中的误判和漏判
4. 需要时再针对 OCR 预处理和模板匹配做增强
5. 最后再考虑攻击逻辑和更复杂的战斗规则

## 9. 运行与标注说明

主程序启动命令未变：

- `python -m bot.main --deck-index 1 --resolution 1440x900`
- `python -m bot.main --deck-index 1 --resolution 1600x900`

开启 OCR 自动采样：

- `python -m bot.main --deck-index 1 --resolution 1440x900 --ocr-auto-sample`

标注工具：

- `python -m bot.ocr_label_mana_main`
- `python -m bot.ocr_label_cost_main`

注意：

- OCR 数据集会在 bot 启动时加载一次
- 标注完成后需要重启 bot，新标注数据才会生效
