using CommunityToolkit.Mvvm.ComponentModel;
using System.Collections.ObjectModel;

namespace AiConstruction.Model
{
    /// <summary>
    /// 聊天消息 — 用于气泡布局，支持流式更新
    /// </summary>
    public partial class ChatMessage : ObservableObject
    {
        #region 基础属性

        /// <summary>true=用户消息（右侧）, false=AI 消息（左侧）</summary>
        public bool IsUser { get; set; }

        /// <summary>消息内容（AI 流式打字时实时更新，仅含 assistant_message 文本）</summary>
        private string _content = string.Empty;
        public string Content
        {
            get => _content;
            set => SetProperty(ref _content, value);
        }

        /// <summary>AI 是否正在思考（等待回复中）</summary>
        private bool _isThinking;
        public bool IsThinking
        {
            get => _isThinking;
            set => SetProperty(ref _isThinking, value);
        }

        /// <summary>思考中显示的提示文字（如"AI 正在思考"或"🔧 正在调用工具"）</summary>
        private string _thinkingText = "AI 正在思考";
        public string ThinkingText
        {
            get => _thinkingText;
            set => SetProperty(ref _thinkingText, value);
        }

        #endregion

        #region 工具步骤（可折叠列表）

        /// <summary>本轮对话中的工具调用步骤列表</summary>
        public ObservableCollection<ToolStep> Steps { get; } = new();

        /// <summary>步骤列表是否展开</summary>
        private bool _isStepsExpanded;
        public bool IsStepsExpanded
        {
            get => _isStepsExpanded;
            set => SetProperty(ref _isStepsExpanded, value);
        }

        /// <summary>折叠头显示文字（如"深度思考"或"已完成 3m35s"）</summary>
        private string _stepsHeaderText = "深度思考";
        public string StepsHeaderText
        {
            get => _stepsHeaderText;
            set => SetProperty(ref _stepsHeaderText, value);
        }

        /// <summary>步骤开始时间（用于计算耗时）</summary>
        public DateTime? StepsStartTime { get; set; }

        #endregion
    }
}
