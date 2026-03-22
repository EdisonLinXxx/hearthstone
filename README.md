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
- 能处理起手换牌确认
- 能在对局内尝试基础出牌
- 无牌可出时能结束回合
- 能处理结果页、继续页、确认页
- 能返回队列继续下一局
- 能通过 `F8` 停止
- 已支持 `1600x900` / `1440x900` 两套固定分辨率 profile
- 已支持按 `--resolution` 选择 profile 对应的窗口尺寸、regions、templates、OCR 配置
- 已加入画面停滞 watchdog 和业务停滞 watchdog，长时间无进展时会执行兜底点击
- 已增加关键运行日志：场景分数、点击坐标、动作选择、拖拽目标

## 6. 当前实现策略

主要场景：

- `startup`
- `main_menu`
- `battle_menu`
- `queue_page`
- `mulligan`
- `battle`
- `result_continue`
- `result`
- `unknown`

对局内当前不是费用驱动决策，而是近似规则：

- 在手牌区检测高亮候选
- 估计可出的牌
- 每次动作前重新计算候选
- 同回合失败牌加入黑名单
- 卡住后强制结束回合
- `traditional_battle_button` 点击点使用匹配框下半部，避免点击到按钮上沿装饰
- 不再对 `queue_play_button` / `end_turn` 使用固定像素偏移

## 7. 当前已知薄弱点

- 手牌候选识别仍有误检/漏检
- 结果页识别仍依赖运行时兜底
- `unknown` 和 `battle` 边界还不够干净
- 目前没有真正费用 OCR
- 没有攻击逻辑
- `1440x900` 虽已可用，但仍依赖实机采样持续微调

## 8. 修改代码时的注意事项

- 不要重新引入针对特定分辨率的固定像素偏移
- 每次点击或拖拽后保留鼠标停靠点逻辑，避免 hover 干扰
- 不要重新引入 `match_count`
- 如果改结果页逻辑，优先减少误判，不要只靠模板阈值硬顶
- 如果改对局逻辑，优先降低误检，再考虑复杂策略

## 9. 建议后续顺序

1. 先收紧 `bot/vision/board_state.py` 的手牌检测
2. 再简化并重构 `bot/runtime.py` 里的结果页兜底逻辑
3. 然后恢复数字 OCR，只做费用识别
4. 最后再考虑攻击逻辑和更完整的战斗规则

## 10. 交接结论

这个工程当前已经能跑通一条基础自动化链路，但本质上仍是 MVP。

后续优先把注意力放在：

- 战斗内识别稳定性
- 结果页识别干净度
- 运行时兜底逻辑去复杂化
