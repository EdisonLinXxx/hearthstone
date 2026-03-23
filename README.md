# 炉石传说脚本

## 1. 项目概况

这是一个运行在 Windows 桌面环境下的炉石传说自动对战 Bot，当前采用：

- 前台截图
- 前台输入
- 固定分辨率 profile：`1600x900` / `1440x900`
- 固定窗口化
- 简体中文客户端
- 休闲模式

当前目标不是高胜率，而是稳定跑通完整闭环：

- 进入游戏
- 进入对战
- 选择套牌
- 开始匹配
- 起手确认
- 基础出牌
- 结束回合
- 处理结算
- 回到队列继续

## 2. 启动方式

依赖安装：

```powershell
pip install -r requirements.txt
```

主程序：

```powershell
python -m bot.main --deck-index 1 --resolution 1600x900
python -m bot.main --deck-index 1 --resolution 1440x900
```

采样工具：

```powershell
python -m bot.sample_main --tag in_battle --deck-index 1 --resolution 1600x900
python -m bot.sample_main --tag in_battle --deck-index 1 --resolution 1440x900
```

停止方式：

```text
F8
```

## 3. 当前固定约束

- 操作系统：Windows
- 分辨率：`1600x900` / `1440x900`
- 窗口位置：`(20, 20)`
- 模式：窗口化
- 游戏语言：简体中文
- 目标模式：休闲模式
- 套牌选择：`deck_index=1-9`

不要默认支持：

- 动态分辨率
- 后台截图
- 后台输入
- 中文卡名 OCR

## 4. 关键文件

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

- `bot/config.py`
- `bot/regions/1600x900.yaml`
- `bot/regions/1440x900.yaml`
- `bot/templates/1600x900/templates.yaml`
- `bot/templates/1440x900/templates.yaml`
- `bot/ocr/1600x900.yaml`
- `bot/ocr/1440x900.yaml`

窗口与采样：

- `bot/capture.py`
- `bot/sampler.py`
- `bot/sample_main.py`

## 5. 当前已实现能力

- 能识别并归位炉石窗口
- 能处理 `炉石传说` / `Hearthstone` 两种标题
- 能走通主界面到对战流程
- 能点击传统对战
- 能选择 `deck_index=1-9`
- 能点击开始匹配
- 能处理匹配失败弹窗并点击中间确认
- 能处理起手换牌确认
- 能在对局内尝试基础出牌
- 无牌可出时能结束回合
- 能处理结果页、继续页、确认页
- 能返回队列继续下一局
- 能通过 `F8` 停止
- 已支持 `1600x900` / `1440x900` 两套固定分辨率 profile
- 已支持按 `--resolution` 选择 profile 对应的窗口尺寸、regions、templates、OCR 配置
- 已加入画面停滞 watchdog 和业务停滞 watchdog，长时间无进展时会执行兜底点击
- 已增加关键运行日志：场景分数、点击坐标、动作选择、拖拽目标、场景归一化原因

## 6. 当前实现策略

主要场景：

- `startup`
- `main_menu`
- `battle_menu`
- `queue_page`
- `matching`
- `match_error`
- `mulligan`
- `battle`
- `result_continue`
- `result`
- `unknown`

对局内当前不是费用驱动决策，而是近似规则：

- 在手牌区检测高亮候选
- 用分层手牌检测链路生成候选：绿色掩码 -> 原始候选 -> 宽连体拆峰值 -> 去重 -> 可出评分
- 每次动作前重新计算候选
- 同回合失败牌加入黑名单
- 卡住后强制结束回合
- `traditional_battle_button` 点击点使用匹配框下半部，避免点击到按钮上沿装饰
- 不再对 `queue_play_button` / `end_turn` 使用固定像素偏移

## 7. 本轮已完成优化

手牌检测：

- 将 `bot/vision/board_state.py` 重构为分层检测链路
- 引入 `hand_detection` YAML 配置，按 profile 管理阈值
- 去掉旧的“超宽连通域等宽切块”逻辑
- 新增“宽连体高亮按横向峰值拆中心”
- 新增自适应去重，减少密集手牌误合并
- `1440x900` 已离线校准到：
  - 样本一：人工 6 张亮绿，可检测 6 张
  - 满手牌密集场景：已针对高密度排列收紧去重逻辑

结果页与异常恢复：

- `confirm` 不再在 `scene.py` 里直接等价于 `result`
- 增加中性场景 `confirm_dialog`
- 在 `runtime.py` 中统一归一化：
  - `confirm_dialog -> match_error`
  - `confirm_dialog -> result`
  - `unknown/battle -> result_continue`
  - `unknown -> battle`
- 增加场景归一化原因日志，便于实机观察

## 8. 当前已知薄弱点

- 手牌候选识别仍可能有误检/漏检
- `1440x900` 仍需要继续用实机样本校准
- 结果页与异常弹窗虽然已收敛，但仍需实机验证边界
- 目前没有真正费用 OCR
- 没有攻击逻辑

## 9. 修改代码时的注意事项

- 不要重新引入针对特定分辨率的固定像素偏移
- 每次点击或拖拽后保留鼠标停靠点逻辑，避免 hover 干扰
- 不要重新引入 `match_count`
- 如果改结果页或异常弹窗逻辑，优先减少误判，不要只靠模板阈值硬顶
- 如果改对局逻辑，优先降低误检，再考虑复杂策略
- `confirm_dialog` 的最终归类应放在 `runtime.py` 做，不要回退成静态硬编码 `confirm == result`

## 10. 建议后续顺序

1. 继续用实机样本验证并微调 `1440x900` 的 battle / result / matching / match_error
2. 恢复数字 OCR，只做费用识别
3. 增加更稳的出牌目标和攻击逻辑
4. 最后再考虑更完整的战斗规则

## 11. 交接结论

这个工程当前已经能跑通一条基础自动化链路，但本质上仍是 MVP。

后续优先把注意力放在：

- 战斗内识别稳定性
- 结果页与异常弹窗识别干净度
- 运行时兜底逻辑继续去复杂化
