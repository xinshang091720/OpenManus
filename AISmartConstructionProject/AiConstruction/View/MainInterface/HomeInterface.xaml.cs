using AiConstruction.ViewModel;
using System.Diagnostics;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Input;

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
        public HomeInterface()
        {
           
            this.DataContext = new HomeInterfaceVM();
            InitializeComponent();
            if (DataContext is HomeInterfaceVM viewModel)
            {
                viewModel.ScrollViewer = scrollViewer;
            }

        
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
            // 监听窗口尺寸变化，拖动缩放时实时更新状态
            this.SizeChanged += Window_SizeChanged;
            //设置托盘
            string icoPath = System.IO.Path.Combine(AppContext.BaseDirectory, "logo.ico");
            notifyIcon = new System.Windows.Forms.NotifyIcon
            {
                Icon = new Icon(icoPath), // 设置图标路径
                Text = "AI智建", // 设置提示文本
                Visible = true // 设置为可见
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

    }
}
