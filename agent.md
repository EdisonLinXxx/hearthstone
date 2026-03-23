# Hearthstone Bot Agent Notes

## 1. 项目概况

这是一个运行在 Windows 桌面环境下的炉石传说自动对战 Bot。当前目标不是高胜率，而是稳定跑通：

- 进入游戏
- 进入对战
- 选择套牌
- 开始匹配
- 处理匹配失败弹窗
- 起手确认
- 基础出牌
- 结束回合
- 处理结果页
- 回到队列继续

## 2. 当前固定约束

- 仅支持 Windows
- 仅支持窗口化
- 仅支持简体中文客户端
- 仅支持 `1600x900` / `1440x900`
- 窗口位置固定为 `(20, 20)`
- 目标模式固定为休闲模式
- 套牌选择固定为 `deck_index=1-9`

默认不要扩展到：

- 动态分辨率
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

### 手牌检测

- `board_state.py` 已从单层绿色阈值检测改为分层链路：
  - 绿色掩码
  - 原始候选
  - 宽连体拆峰值
  - 自适应去重
  - 可出评分
- `hand_detection` 阈值已进入 `regions/*.yaml`
- `1440x900` 当前是优先 profile，已经围绕实机样本持续调参

### 异常恢复

- 新增 `match_error` 处理，匹配失败弹窗点击中间确认关闭
- 结果页与异常弹窗识别已在 `runtime.py` 内部统一收敛
- 新增场景归一化原因日志，便于直接从日志判断为什么改判

## 6. 当前已知风险

- `1440x900` 仍需继续实机样本校准
- 密集手牌场景仍有继续微调空间
- 结果页 / 异常弹窗边界还需要更多日志验证
- 费用 OCR 尚未恢复
- 攻击逻辑尚未实现

## 7. 修改代码时的原则

- 不要重新引入固定像素偏移
- 不要重新引入 `match_count`
- 点击/拖拽后保留鼠标停靠逻辑，避免 hover 干扰
- 如果改 `runtime.py`，优先把判定收敛到统一入口，不要散落在多个兜底分支
- 如果改 `board_state.py`，优先降低误检，再考虑提高召回
- 如果改异常弹窗逻辑，优先保住 `queue/matching`、`result`、`battle` 三类上下文边界

## 8. 当前建议顺序

1. 继续验证 `1440x900` 下的 `battle / result / match_error`
2. 恢复 mana OCR，只做费用识别
3. 再考虑攻击逻辑和更完整的战斗规则
