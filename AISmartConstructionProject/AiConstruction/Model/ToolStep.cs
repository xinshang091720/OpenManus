using CommunityToolkit.Mvvm.ComponentModel;

namespace AiConstruction.Model
{
    /// <summary>
    /// 工具调用步骤 — 在 AI 消息中以可折叠列表的形式展示
    /// </summary>
    public partial class ToolStep : ObservableObject
    {
        /// <summary>步骤标题（始终显示）</summary>
        public string Title { get; set; } = string.Empty;

        /// <summary>展开后的详细描述（执行结果）</summary>
        public string Content { get; set; } = string.Empty;

        /// <summary>状态图标（🔧 启动中 / ✅ 成功 / ❌ 失败）</summary>
        public string Icon { get; set; } = "🔧";

        /// <summary>是否执行成功</summary>
        public bool IsSuccess { get; set; }

        /// <summary>是否已完成（tool_completed 后为 true）</summary>
        public bool IsCompleted { get; set; }

        /// <summary>步骤编号（用于 [1] [2] [3] 角标）</summary>
        public int StepNumber { get; set; }

    }
}
