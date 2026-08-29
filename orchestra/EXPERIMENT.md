# agent-orchestra 真机实验指引（MVP 终验）

## 前置

- kb 服务运行中：`python -m kb serve`（另开终端保持）
- orchestra-worker skill 已安装（Task 8）
- 本仓切到 orchestra 分支

## 步骤

1. **提需求**：在协调者任务（本任务）对 AI 说一个小型开发需求，
   例如"给 orchestra 加一个 board.py list-pending 子命令"
2. **协调者拆卡**：观察 AI 用 board.py add 建 2-3 张卡（assignee=worker-1）
3. **开 worker 任务**：新开一个 TraeWork 任务（模型任选），
   粘贴 `board.py new-worker worker-1` 的输出引导语
4. **观察 worker**：它应加载 orchestra-worker skill → 领卡 → 执行 → 回写
5. **核验**：回协调者任务说"核验"，AI 用 board.py verify 流转
6. **检查点**：
   - [ ] 卡片全程符合字符上限（show 抽查）
   - [ ] worker 单卡单轮，无轮询
   - [ ] 协调者只靠 status/show 汇报（上下文增长受控）
   - [ ] 全链路 pending→…→verified 走通

## 失败排查

- worker 查不到卡 → 确认它挂载了 kb MCP、名字与 assignee 一致
- verify 报状态错 → 卡未到 done/failed，先看 worker 是否回写
- 全部卡卡死 claimed → 等 30 分钟超时或人工 reject
