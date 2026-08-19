using AiConstruction.Api;
using AiConstruction.Services;
using System;
using System.Windows;
using System.Windows.Media;

namespace AiConstruction
{
    /// <summary>
    /// Interaction logic for App.xaml
    /// </summary>
    public partial class App : Application
    {
        /// <summary>Runtime 进程管理器（全局单例）</summary>
        public static RuntimeManager Runtime { get; } = new RuntimeManager();

        /// <summary>Revit 端口检测器（全局单例）</summary>
        public static RevitPortDetector PortDetector { get; } = new RevitPortDetector();

        /// <summary>Revit API HTTP 代理（全局单例）</summary>
        public static RevitProxyService Proxy { get; } = new RevitProxyService(PortDetector);

        protected override void OnStartup(StartupEventArgs e)
        {
            base.OnStartup(e);

            LogHelper.Info($"[应用] 启动完成，API 地址: {ApiConfig.BaseUrl}");

            // 强制覆盖 MarkdView 主题中所有蓝色系资源键
            OverrideMarkdViewColors();

            // 启动 Revit 端口检测和 HTTP 代理（不阻塞 UI）
            PortDetector.Start();
            Proxy.Start();

            // 异步启动 Runtime（不阻塞 UI）
            _ = StartRuntimeAsync();
        }

        protected override void OnExit(ExitEventArgs e)
        {
            Runtime.Dispose();
            Proxy.Dispose();
            PortDetector.Dispose();
            LogHelper.Info("[应用] 已退出");
            base.OnExit(e);
        }

        private async System.Threading.Tasks.Task StartRuntimeAsync()
        {
            try
            {
                LogHelper.Info("[应用] 正在启动 Runtime...");
                var success = await Runtime.StartAsync();

                if (success)
                {
                    ApiConfig.BaseUrl = Runtime.BaseUrl;
                    LogHelper.Info($"[应用] Runtime 就绪: {Runtime.BaseUrl}");
                }
                else
                {
                    LogHelper.Warn("[应用] Runtime 启动失败，使用默认配置");
                }
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[应用] Runtime 启动异常: {ex}");
            }
        }

        /// <summary>
        /// 用简约深灰配色覆盖 MarkdView Light 主题中的蓝色/彩色资源键
        /// </summary>
        private void OverrideMarkdViewColors()
        {
            // 正文与背景
            Resources["Markdown.Foreground"] = new SolidColorBrush(Color.FromRgb(0x33, 0x33, 0x33));
            Resources["Markdown.Background"] = new SolidColorBrush(Colors.Transparent);

            // 粗体 —— MarkdView Light 主题默认 #4C63EB（蓝色），改为深灰
            Resources["Markdown.Bold.Foreground"] = new SolidColorBrush(Color.FromRgb(0x22, 0x22, 0x22));

            // 链接 —— #4C63EB → 中灰
            Resources["Markdown.Link.Foreground"] = new SolidColorBrush(Color.FromRgb(0x55, 0x55, 0x55));

            // 标题
            Resources["Markdown.Heading.H1.Foreground"] = new SolidColorBrush(Color.FromRgb(0x22, 0x22, 0x22));
            Resources["Markdown.Heading.H1.Border"] = new SolidColorBrush(Color.FromRgb(0xDD, 0xDD, 0xDD));
            Resources["Markdown.Heading.H2.Foreground"] = new SolidColorBrush(Color.FromRgb(0x33, 0x33, 0x33));
            Resources["Markdown.Heading.H3.Foreground"] = new SolidColorBrush(Color.FromRgb(0x44, 0x44, 0x44));

            // 引用 —— 去掉背景和边框，显示为正常文本
            Resources["Markdown.Quote.Background"] = new SolidColorBrush(Colors.Transparent);
            Resources["Markdown.Quote.Border"] = new SolidColorBrush(Colors.Transparent);

            // 代码块
            Resources["Markdown.CodeBlock.Background"] = new SolidColorBrush(Color.FromRgb(0xF0, 0xF0, 0xF0));
            Resources["Markdown.CodeBlock.Foreground"] = new SolidColorBrush(Color.FromRgb(0x33, 0x33, 0x33));
            Resources["Markdown.CodeBlock.Header.Background"] = new SolidColorBrush(Color.FromRgb(0xE8, 0xE8, 0xE8));

            // 代码块复制按钮
            Resources["Markdown.CodeBlock.CopyButton.Background"] = new SolidColorBrush(Color.FromRgb(0x66, 0x66, 0x66));
            Resources["Markdown.CodeBlock.CopyButton.Foreground"] = new SolidColorBrush(Colors.White);

            // 行内代码 —— 去掉背景/边框/圆角
            Resources["Markdown.InlineCode.Background"] = new SolidColorBrush(Colors.Transparent);
            Resources["Markdown.InlineCode.Foreground"] = new SolidColorBrush(Color.FromRgb(0x22, 0x22, 0x22));
            Resources["Markdown.InlineCode.Border"] = new SolidColorBrush(Colors.Transparent);
            Resources["Markdown.InlineCode.BorderThickness"] = new Thickness(0);
            Resources["Markdown.InlineCode.CornerRadius"] = new CornerRadius(0);

            // 语法高亮
            Resources["Markdown.Syntax.Default"] = new SolidColorBrush(Color.FromRgb(0x33, 0x33, 0x33));
            Resources["Markdown.Syntax.Comment"] = new SolidColorBrush(Color.FromRgb(0x99, 0x99, 0x99));
            Resources["Markdown.Syntax.String"] = new SolidColorBrush(Color.FromRgb(0x66, 0x66, 0x66));
            Resources["Markdown.Syntax.Function"] = new SolidColorBrush(Color.FromRgb(0x55, 0x55, 0x55));
            Resources["Markdown.Syntax.ControlKeyword"] = new SolidColorBrush(Color.FromRgb(0x44, 0x44, 0x44));
            Resources["Markdown.Syntax.DeclarationKeyword"] = new SolidColorBrush(Color.FromRgb(0x44, 0x44, 0x44));
            Resources["Markdown.Syntax.TypeKeyword"] = new SolidColorBrush(Color.FromRgb(0x44, 0x44, 0x44));
            Resources["Markdown.Syntax.Type"] = new SolidColorBrush(Color.FromRgb(0x55, 0x55, 0x55));
            Resources["Markdown.Syntax.Number"] = new SolidColorBrush(Color.FromRgb(0x66, 0x66, 0x66));
            Resources["Markdown.Syntax.Attribute"] = new SolidColorBrush(Color.FromRgb(0x66, 0x66, 0x66));
            Resources["Markdown.Syntax.Literal"] = new SolidColorBrush(Color.FromRgb(0x33, 0x33, 0x33));
            Resources["Markdown.Syntax.ShellCommand"] = new SolidColorBrush(Color.FromRgb(0x55, 0x55, 0x55));
        }
    }
}
