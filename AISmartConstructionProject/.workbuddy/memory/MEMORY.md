# AI 建项目长期记忆

## 对话历史保存策略

`_history` 与 `AddDialogueHistory` 用途不同，必须区分数据源：

- **`_history`**：本地内存中的上下文，传给 BeeSync.AgentRuntime 作为下一轮对话的 `History`。
  - 只保存 `run_completed` 的 `final_answer`（`finalAssistantContent`）。
  - 不保存 `assistant_message` 中的 plan/reasoning 等思考过程内容，避免污染模型上下文。

- **`AddDialogueHistory`**：调用后台接口保存聊天记录，用于历史列表重新加载展示。
  - 保存用户实际看到的气泡内容（`currentAiTextBubble.Content` 经 `CleanContentSteps` 处理）。
  - 必须包含完整文本和 Markdown 格式，确保重新加载后与实时回复显示一致。

**关键教训**：`run_completed.final_answer` 与 UI 气泡显示内容可能不一致（如缺少 plan 前置文本、格式差异），不能把它们当作同一内容使用。
