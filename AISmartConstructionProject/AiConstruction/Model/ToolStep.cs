using CommunityToolkit.Mvvm.ComponentModel;

namespace AiConstruction.Model
{
    /// <summary>
    /// 工具调用步骤 — 在 AI 消息中以可折叠列表的形式展示
    /// </summary>
    public partial class ToolStep : ObservableObject
    {
        /// <summary>步骤标题（始终显示）</summary>
        private string _title = string.Empty;
        public string Title
        {
            get => _title;
            set => SetProperty(ref _title, value);
        }

        /// <summary>展开后的详细描述（执行结果）</summary>
        private string _content = string.Empty;
        public string Content
        {
            get => _content;
            set => SetProperty(ref _content, value);
        }

        /// <summary>状态图标（🔧 启动中 / ✅ 成功 / ❌ 失败）</summary>
        private string _icon = "🔧";
        public string Icon
        {
            get => _icon;
            set => SetProperty(ref _icon, value);
        }

        /// <summary>Runtime 工具名，用于把进度和完成事件更新到同一步骤</summary>
        public string Tool { get; set; } = string.Empty;

        /// <summary>是否执行成功</summary>
        private bool _isSuccess;
        public bool IsSuccess
        {
            get => _isSuccess;
            set => SetProperty(ref _isSuccess, value);
        }

        /// <summary>是否已完成（tool_completed 后为 true）</summary>
        private bool _isCompleted;
        public bool IsCompleted
        {
            get => _isCompleted;
            set => SetProperty(ref _isCompleted, value);
        }

        /// <summary>步骤编号（用于 [1] [2] [3] 角标）</summary>
        public int StepNumber { get; set; }

    }
}
