using AiConstruction.Api;
using AiConstruction.Model;
using AiConstruction.Services;
using AiConstruction.Updata;
using AiConstruction.View;
using AiConstruction.View.MainInterface;
using System;
using System.Text;
using System.Text.Json;
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

        /// <summary>注册 .NET Core 缺失的编码提供程序（GBK 等）</summary>
        static App()
        {
            Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
        }

        protected  override  void OnStartup(StartupEventArgs e)
        {
            // 全局异常捕获：防止任何未处理异常导致静默闪退
            DispatcherUnhandledException += (s, args) =>
            {
                LogHelper.Error($"[应用] UI 线程未处理异常: {args.Exception}");
                args.Handled = true;
            };
            AppDomain.CurrentDomain.UnhandledException += (s, args) =>
            {
                LogHelper.Error($"[应用] 未处理异常(非UI线程): {args.ExceptionObject}");
            };
            System.Threading.Tasks.TaskScheduler.UnobservedTaskException += (s, args) =>
            {
                LogHelper.Error($"[应用] 未观察到的任务异常: {args.Exception}");
                args.SetObserved();
            };

            base.OnStartup(e);
            LogHelper.Info($"[应用] 启动完成，API 地址: {ApiConfig.BaseUrl}");

            //LoadSavedLoginInfo();
            //if (!string.IsNullOrEmpty(ApiConfig.AccountToken))
            //{
            //    AccountApiClient _apiClient = new();
            //    var data = _apiClient.VerifyToken();
            //    if (data.Code == 500)
            //    {
            //        ShutdownMode = ShutdownMode.OnExplicitShutdown;  // ← 关键
            //        Login login = new Login();
            //        login.ShowDialog();
            //        ShutdownMode = ShutdownMode.OnMainWindowClose;   // ← 恢复
            //    }
            //}
            //else
            //{
            //    ShutdownMode = ShutdownMode.OnExplicitShutdown;  // ← 关键
            //    Login login = new Login();
            //    login.ShowDialog();
            //    ShutdownMode = ShutdownMode.OnMainWindowClose;   // ← 恢复
            //}
            //var isAiUpdate = CheckUpdata.AiConstructionUpdate("AI智建");
            //var isConstructionUpdate = CheckUpdata.ConstructionUpdate("报建系列");
            //if (!isAiUpdate && isConstructionUpdate)
            //{
            //    MessageBox.Show("检测到当前AI智建与SwarmBIM报建不是最新版本,是否进行更新？","提示",MessageBoxButton.OKCancel,MessageBoxImage.Information)
            //}
            // 强制覆盖 MarkdView 主题中所有蓝色系资源键
            OverrideMarkdViewColors();

            //// 启动 Revit 端口检测和 HTTP 代理（不阻塞 UI）
            PortDetector.Start();
            Proxy.Start();

            // 异步启动 Runtime（不阻塞 UI）
            _ = StartRuntimeAsync();

            // 异步检测外网连通性（不阻塞 UI）：本机无法上网时弹窗提示，可重试
           // _ = NetworkHelper.CheckNetworkAndNotifyAsync();
        }

        /// <summary>
        /// 从程序同目录 login.dat 加载上次保存的登录信息
        /// </summary>
        public void LoadSavedLoginInfo()
        {
            try
            {
                var path = ApiConfig.LoginDataPath;
                if (!System.IO.File.Exists(path))
                    return;

                var encrypted = System.IO.File.ReadAllText(path);
                var json = ApiConfig.DecryptString(encrypted);
                if (string.IsNullOrEmpty(json))
                    return;

                var info = JsonSerializer.Deserialize<SavedLoginInfo>(json);
                if (info == null)
                    return;
                ApiConfig.AccountToken = info.AccountToken;
                ApiConfig.UserId = info.UserId ?? string.Empty;
                ApiConfig.Authorise = info.Authorise;
                ApiConfig.UserName= info.UserName ?? string.Empty;
                ApiConfig.Phone = info.Phone ?? string.Empty;
                ApiConfig.Password = info.Password ?? string.Empty;
            }
            catch
            {
                // 读取失败静默忽略，不影响正常登录
            }
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
