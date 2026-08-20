using AiConstruction.Model;
using AiConstruction.ViewModel;
using System.Diagnostics;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using Key = System.Windows.Input.Key;

namespace AiConstruction.View.MainInterface
{
    /// <summary>
    /// HomeInterface.xaml 的交互逻辑
    /// </summary>
    public partial class HomeInterface : Window
    {
        private System.Windows.Forms.NotifyIcon notifyIcon;
        [DllImport("USER32.DLL", SetLastError = true, CharSet = CharSet.Auto)]
        public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool IsIconic(IntPtr hWnd);

        [DllImport("user32.dll")]
        static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        const int SW_RESTORE = 9;


        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool SetForegroundWindow(IntPtr hWnd);
        private HomeInterfaceVM _vm = null!;
        public HomeInterface()
        {
            _vm = new HomeInterfaceVM(this);
            this.DataContext = _vm;
            InitializeComponent();
            this.Closed += HomeInterface_Closed;
            // 注入遮罩层显示/隐藏委托（弹窗时底层变灰）
            _vm.SetMaskVisibilityAction = (visible) =>
            {
                if (FindName("MaskLayer") is System.Windows.Controls.Border mask)
                {
                    mask.Visibility = visible ? Visibility.Visible : Visibility.Collapsed;
                    if (visible)
                        this.UpdateLayout();  // 🔑 强制同步布局+渲染
                }
            };
            if (DataContext is HomeInterfaceVM viewModel)
            {
                viewModel.ScrollViewer = scrollViewer;
            }
            IsProcess();
            // 监听窗口尺寸变化，拖动缩放时实时更新状态
            this.SizeChanged += Window_SizeChanged;
            //设置托盘
            string icoPath = System.IO.Path.Combine(AppContext.BaseDirectory, "logo.ico");
            System.Drawing.Icon? trayIcon = null;
            if (System.IO.File.Exists(icoPath))
            {
                try { trayIcon = new System.Drawing.Icon(icoPath); }
                catch { }
            }
            notifyIcon = new System.Windows.Forms.NotifyIcon
            {
                Icon = trayIcon,
                Text = "AI智建",
                Visible = true
            };
            // 创建一个上下文菜单
            var menuStrip = new System.Windows.Forms.ContextMenuStrip();
            menuStrip.Items.Add("显示", null, (s, e) => ShowWindow());
            menuStrip.Items.Add("隐藏", null, (s, e) => HideWindow());
            menuStrip.Items.Add("退出", null, (s, e) => ExitApplication());
            // 将上下文菜单附加到 NotifyIcon
            notifyIcon.ContextMenuStrip = menuStrip;
            notifyIcon.MouseDoubleClick += (s, e) => ShowWindow();
        }

        private void HomeInterface_Closed(object? sender, EventArgs e)
        {
            notifyIcon.Visible = false;  // 必须先设为 false
            notifyIcon.Dispose();        // 然后释放资源
        }

        public void IsProcess()
        {
            Process[] processes = Process.GetProcessesByName("AiConstruction");
            if (processes.Length > 1)
            {
                // 激活已存在的实例
                foreach (var process in processes)
                {
                    if (process.Id != Process.GetCurrentProcess().Id)
                    {
                        IntPtr hWnd = FindWindow(null, "AI智建");
                        if (hWnd != IntPtr.Zero)
                        {
                            // 检查窗口是否最小化
                            if (IsIconic(hWnd))
                            {
                                ShowWindow(hWnd, SW_RESTORE); // 恢复窗口
                            }
                            // 激活窗口
                            SetForegroundWindow(hWnd);
                            Environment.Exit(0);
                            return;
                        }

                    }
                }
            }
        }

        private void Window_SizeChanged(object sender, SizeChangedEventArgs e)
        {
            // 仅同步按钮勾选，WindowState 由系统自动维护
            bool isMax = this.WindowState == WindowState.Maximized;
            MaxBtn.IsChecked = isMax;
        }

        private void Grid_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            DragMove();
        }
        private void BtnMax_Checked(object sender, RoutedEventArgs e)
        {
            WindowState = WindowState.Maximized;
        }

        private void BtnMax_Unchecked(object sender, RoutedEventArgs e)
        {
            WindowState = WindowState.Normal;
        }
        private void HideWindow()
        {
            this.ShowInTaskbar = false;
            this.Hide();
        }

        private void ShowWindow()
        {
            this.Show();
            this.WindowState = WindowState.Normal;
        }
        private void ExitApplication()
        {
            notifyIcon.Visible = false;
            this.Close();
            Application.Current.Shutdown();
        }

        /// <summary>
        /// 输入框回车键处理：Enter 发送，Shift+Enter 换行
        /// </summary>
        private void SendTextBox_PreviewKeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter && Keyboard.Modifiers != ModifierKeys.Shift)
            {
                e.Handled = true;

                if (DataContext is HomeInterfaceVM vm && !vm.IsLoading && !string.IsNullOrWhiteSpace(vm.SendContent))
                {
                    vm.SendContentCommand.Execute(SendTextBox);
                }
            }
        }

        /// <summary>
        /// 步骤列表头部点击 — 整体展开/折叠
        /// </summary>
        private void StepsHeader_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
        {
            if (sender is not System.Windows.Controls.Grid header) return;
            if (header.DataContext is not Model.ChatMessage msg) return;

            msg.IsStepsExpanded = !msg.IsStepsExpanded;
        }

     
        /// <summary>
        /// 分组子项或叶子节点点击 → 选中并加载历史对话
        /// </summary>
        private void GroupHistoryItem_Click(object sender, RoutedEventArgs e)
        {
            if (sender is not Button border) return;

            var item = border.DataContext switch
            {
                // 子项（在 Children 内）：DataContext 直接是 GroupHistory
                Model.GroupHistory gh => gh,
                // 叶子节点：DataContext 是 GroupHistoryNode
                Model.GroupHistoryNode node => node.Item,
                _ => null
            };

            if (item != null && DataContext is HomeInterfaceVM vm)
            {
                vm.SelectGroupItemCommand.Execute(item);
            }
        }
    }
}
