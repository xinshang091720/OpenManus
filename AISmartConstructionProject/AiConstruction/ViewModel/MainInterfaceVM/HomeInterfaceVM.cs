using AiConstruction.Api;
using AiConstruction.Model;
using AiConstruction.Services;
using AiConstruction.Updata;
using AiConstruction.View;
using AiConstruction.View.MainInterface;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Net.Mail;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using static System.Windows.Forms.VisualStyles.VisualStyleElement.StartPanel;

namespace AiConstruction.ViewModel
{
    public partial class HomeInterfaceVM : ObservableObject
    {

        public HomeInterface HomeInterface { get; set; }
        private CancellationTokenSource? _runCts;
        /// <summary>聊天消息列表 消息集合（气泡聊天核心）</summary> 
        public ObservableCollection<ChatMessage> Messages { get; } = new();
        public string finalAssistantContent = string.Empty;
        public CreateRunResponse? createResponse = new();
        // 当前活跃的 AI 气泡（StopCommand 需要访问其 Content 来保存用户实际看到的内容）
        public ChatMessage? currentAiTextBubble;
        public HomeInterfaceVM(HomeInterface homeInterface)

        {
            LoadSavedLoginInfo();
            HomeInterface = homeInterface;
            _accountApi = new();
          
            homeInterface.Loaded += HomeInterface_Loaded;

        }
  
        private  void HomeInterface_Loaded(object sender, RoutedEventArgs e)
        {
            try
            {
                Application.Current.Dispatcher.BeginInvoke(async () =>
                {
                    if (!await NetworkHelper.CheckNetworkAndNotifyAsync()) return;
                    if (!string.IsNullOrEmpty(ApiConfig.AccountToken))
                    {
                        var data = await _accountApi.VerifyToken();
                        if (data != null)
                        {
                            if (data.IsSuccess)
                            {
                                NotLoginVisib = Visibility.Collapsed;
                                LoginVisib = Visibility.Visible;
                                AddGroupHistories();
                            }
                            else
                            {
                                NotLoginVisib = Visibility.Visible;
                                LoginVisib = Visibility.Collapsed;
                            }
                        }
                        else
                        {
                            NotLoginVisib = Visibility.Visible;
                            LoginVisib = Visibility.Collapsed;
                        }
                    }
                    else
                    {
                        NotLoginVisib = Visibility.Visible;
                        LoginVisib = Visibility.Collapsed;
                    }
                    var isAiUpdata = CheckUpdata.AiConstructionUpdate("AI智建");
                    Update(isAiUpdata);
                });

            }
            catch
            {

            }

            //GroupHistories.Add(new GroupHistory { Title = "测试一标题dwadawdawdawdwadwadawdaw" });
            //      GroupHistories.Add(new GroupHistory { Title = "测试二标题" });
        }
     
        #region API 客户端 & 对话上下文

        /// <summary>API 客户端（延迟创建，确保 Runtime 就绪后使用正确端口和 Token）</summary>
        private ApiClient? _apiClient;

        /// <summary>获取或创建 ApiClient（线程安全）</summary>
        private ApiClient ApiClient
        {
            get
            {
                if (_apiClient == null)
                {
                    var httpClient = App.Runtime.CreateApiClient();
                    _apiClient = new ApiClient(httpClient);
                }
                return _apiClient;
            }
        }

        private readonly List<HistoryMessage> _history = new();
        private readonly string _conversationId = $"conv-{DateTime.Now:yyyyMMdd}-{Guid.NewGuid():N}"[..20];
        private string? _currentRunId;
        //对话组ID
        private string sessionGroupId = string.Empty;

        private readonly AccountApiClient _accountApi = new();

        // 控制展开/折叠状态 任务列表

        private bool _isTaskListExpanded = true;
        public bool IsTaskListExpanded
        {
            get => _isTaskListExpanded;
            set => SetProperty(ref _isTaskListExpanded, value);
        }
        // 控制展开/折叠状态 任务列表

        private bool _isSpaceExpanded = true;
        public bool IsSpaceExpanded
        {
            get => _isTaskListExpanded;
            set => SetProperty(ref _isTaskListExpanded, value);
        }
        //选择空间文件
        private bool _seleteSpaceExpanded = false;
        public bool SeleteSpaceExpanded
        {
            get => _seleteSpaceExpanded;
            set => SetProperty(ref _seleteSpaceExpanded, value);
        }
        #endregion

   

        #region UI 绑定属性

        private Visibility hideSidebar = Visibility.Visible;
        public Visibility HideSidebar
        {
            get => hideSidebar;
            set => SetProperty(ref hideSidebar, value);
        }

        private string sendContent = string.Empty;
        public string SendContent
        {
            get => sendContent;
            set => SetProperty(ref sendContent, value);
        }
        //对话标题
        private string dialogueTitle = string.Empty;
        public string DialogueTitle
        {
            get => dialogueTitle;
            set => SetProperty(ref dialogueTitle, value);
        }
        //未登录 

        private Visibility notLoginVisib=Visibility.Collapsed;
        public Visibility NotLoginVisib
        {
            get => notLoginVisib;
            set => SetProperty(ref notLoginVisib, value);
        }
        private Visibility loginVisib;
        public Visibility LoginVisib
        {
            get => loginVisib;
            set => SetProperty(ref loginVisib, value);
        }
        public ScrollViewer? ScrollViewer { get; set; }

        private Visibility initializeTitie = Visibility.Visible;
        public Visibility InitializeTitie
        {
            get => initializeTitie;
            set => SetProperty(ref initializeTitie, value);
        }

        /// <summary>是否有对话内容（控制对话区域可见性）</summary>
        private Visibility dialogueContent = Visibility.Collapsed;
        public Visibility DialogueContent
        {
            get => dialogueContent;
            set => SetProperty(ref dialogueContent, value);
        }

        private int sidebarColumnWidth = 268;
        public int SidebarColumnWidth
        {
            get => sidebarColumnWidth;
            set => SetProperty(ref sidebarColumnWidth, value);
        }

        private bool isLoading;
        public bool IsLoading
        {
            get => isLoading;
            set => SetProperty(ref isLoading, value);
        }

        private string savePath = string.Empty;
        public string SavePath
        {
            get => savePath;
            set => SetProperty(ref savePath, value);
        }
        private string taskTitle="任务";
        public string TaskTitle
        {
            get => taskTitle;
            set => SetProperty(ref taskTitle, value);
        }
        private string userName;
        public string UserName
        {
            get => userName;
            set => SetProperty(ref userName, value);
        }


        private string spaceTitle = "空间";
        public string SpaceTitle
        {
            get => spaceTitle;
            set => SetProperty(ref spaceTitle, value);
        }
        private string sutSpaceName = "选择空间名称";
        public string SutSpaceName
        {
            get => sutSpaceName;
            set => SetProperty(ref sutSpaceName, value);
        }

        private bool _ignoreNextClose; // 忽略下一次自动关闭

        /// <summary>
        /// 控制父窗口遮罩层显示/隐藏（由 View 代码后置注入）
        /// </summary>
        public Action<bool>? SetMaskVisibilityAction { get; set; }
        //弹窗开关 选择文件的弹窗
        private bool _isPopupOpen;
        public bool IsPopupOpen
        {
            get => _isPopupOpen;
            set
            {
                // 如果是被忽略的关闭，直接取消
                if (_ignoreNextClose && !value)
                {
                    _ignoreNextClose = false;
                    return;
                }
                _isPopupOpen = value;
                OnPropertyChanged("IsPopupOpen");
            }
        }
        //选择文件按钮
        private ToggleButton _clickTargetBtn;
        public ToggleButton ClickTargetBtn
        {
            get => _clickTargetBtn;
            set => SetProperty(ref _clickTargetBtn, value);
        }
        //点击用户名称时弹出
        private bool _isUserNamePopupOpen;
        public bool IsUserNamePopupOpen
        {
            get => _isUserNamePopupOpen;
            set
            {
                // 如果是被忽略的关闭，直接取消
                if (_userNextClose && !value)
                {
                    _userNextClose = false;
                    return;
                }
                _isUserNamePopupOpen = value;
                OnPropertyChanged("IsUserNamePopupOpen");
            }
        }
        private bool _userNextClose; // 忽略下一次自动关闭
        //选择用户名称按钮
        private ToggleButton _clickUserNameButton;
        public ToggleButton ClickUserNameButton
        {
            get => _clickUserNameButton;
            set => SetProperty(ref _clickUserNameButton, value);
        }
        //任务列表

        private ObservableCollection<GroupHistory>? _groupTaskHistories;

        public ObservableCollection<GroupHistory>? GroupTaskHistories
        {
            get
            {
                // 懒初始化：第一次访问自动实例化
                return _groupTaskHistories ??= new ObservableCollection<GroupHistory>();
            }
            set
            {
                if (_groupTaskHistories != value)
                {
                    _groupTaskHistories = value;
                    OnPropertyChanged();
                }
            }
        }


        /// <summary>当前选中的空间和任务 记录是否选择同一个项</summary>
        private GroupHistory? _selectedSpaceGroupHistory;
        public GroupHistory? SelectedSapceGroupHistory
        {
            get => _selectedSpaceGroupHistory;
            set
            {
                if (SetProperty(ref _selectedSpaceGroupHistory, value) && value != null)
                {
                    sessionGroupId = value.SessionGroupId;
                    _ = LoadChatHistoryAsync(value);
                }
            }
        }

        /// <summary>侧边栏树形节点列表  空间任务节点数据 </summary>
        public ObservableCollection<GroupHistoryNode> GroupHistoryNodes { get; } = new();

        /// <summary>点击子项 → 选中并加载历史对话（重复点击同一条不触发）</summary>
        [RelayCommand]
        private void SelectGroupItem(GroupHistory? item)
        {
            if (item == null) return;

            // 同一条不重复加载
            if (_selectedSpaceGroupHistory == item) return;

            _selectedSpaceGroupHistory = item;
            OnPropertyChanged(nameof(SelectedSapceGroupHistory));
            sessionGroupId = item.SessionGroupId;
            DialogueTitle = item.Title;
            _ = LoadChatHistoryAsync(item);

        }

        #endregion

   

        #region 命令

        public RelayCommand<Window> MinimizeCommand => new(MinimizeWindow);
        public RelayCommand<Window> CloseCommand => new(MinimizeWindow);  // 关闭实际是最小化到托盘

        private static void MinimizeWindow(Window? window)
        {
            if (window != null) window.WindowState = WindowState.Minimized;
        }

        //显示与隐藏
        public RelayCommand HideSidebarCommand => new(() =>
        {
            if (HideSidebar == Visibility.Visible)
            {
                HideSidebar = Visibility.Collapsed;
                SidebarColumnWidth = 0;
            }
            else
            {
                HideSidebar = Visibility.Visible;
                SidebarColumnWidth = 268;
            }
        });
        // 任务列表
        public RelayCommand ToggleTaskListCommand => new(() =>
        {
            IsTaskListExpanded = !IsTaskListExpanded;
        });
        //空间列表
        public RelayCommand ToggleSpaceListCommand => new(() =>
        {
            IsSpaceExpanded = !IsSpaceExpanded;
        });

        /// <summary>
        /// 新建对话 生成对话组ID
        /// </summary>
        public RelayCommand NewConversationCommand => new(() =>
        {
            sessionGroupId = GenerateTimeId();
            DialogueContent = Visibility.Collapsed;
            InitializeTitie = Visibility.Visible;
            SavePath = string.Empty;
            DialogueTitle = string.Empty;
            SutSpaceName= "选择空间名称";
            Messages.Clear();
            _history.Clear();

        });
        //选择工作空间命令
        public RelayCommand<object> SeleltSpaceCommand => new((item) =>
        {
            try
            {
                if (item is ToggleButton btn)
                {
                    _ignoreNextClose = true;
                    // 同按钮切换关闭
                    if (IsPopupOpen && ClickTargetBtn == btn)
                    {
                        IsPopupOpen = false;

                        ClickTargetBtn = new();
                        return;
                    }
                    ClickTargetBtn = btn;
                    HomeInterface.Dispatcher.BeginInvoke(DispatcherPriority.Loaded, new Action(() =>
                    {
                        IsPopupOpen = true;

                    }));
                    // SeleteSpaceExpanded= !SeleteSpaceExpanded ;
                }
            }
            catch(Exception ex)
            {
                LogHelper.Error($"选择工作空间异常: {ex.Message}");
            }
        });
        //选择用户名称弹出
        public RelayCommand<object> SeleteUserNameCommand => new((item) =>
        {
            try
            {
                if (item is ToggleButton btn)
                {
                    _userNextClose = true;
                    // 同按钮切换关闭
                    if (IsUserNamePopupOpen && ClickUserNameButton == btn)
                    {
                        IsUserNamePopupOpen = false;

                        ClickUserNameButton = new();
                        return;
                    }
                    ClickUserNameButton = btn;
                    HomeInterface.Dispatcher.BeginInvoke(DispatcherPriority.Loaded, new Action(() =>
                    {
                        IsUserNamePopupOpen = true;

                    }));
                    // SeleteSpaceExpanded= !SeleteSpaceExpanded ;
                }
            }
            catch
            {

            }
        });

        //创建空间
        public RelayCommand<Window> CreateWorkSpaceCommand => new((window) =>
        {
            try
            {
                SetMaskVisibilityAction?.Invoke(true);
                var addCreateSpace = new AddCreateSpace { Owner = window };
                AddCreateSpaceVM addCreateSpaceVM = new AddCreateSpaceVM();
                addCreateSpace.DataContext = addCreateSpaceVM;
                addCreateSpace.ShowDialog();
                SetMaskVisibilityAction?.Invoke(false);
                if (addCreateSpaceVM.IsConfirm && !string.IsNullOrEmpty(addCreateSpaceVM.SapceName))
                {
                    // 当前用户根目录 C:\Users\用户名
                    string userFolder = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
                    string folderPath = Path.Combine(userFolder, "AiConstruction");
                    if (!Directory.Exists(folderPath))
                    {
                        Directory.CreateDirectory(folderPath);
                    }
                    SavePath = Path.Combine(folderPath, addCreateSpaceVM.SapceName);
                    if (!Directory.Exists(SavePath))
                    {
                        Directory.CreateDirectory(SavePath);
                        SutSpaceName = addCreateSpaceVM.SapceName;
                    }
                }
            }
            catch
            {

            }
            finally
            {
                IsPopupOpen = false;
            }

        });
        //选择文件夹
        public RelayCommand<Window> SeleteFolderPathCommand => new((window) =>
        {
            try
            {  // 方式一：使用 FolderBrowserDialog (WinForms)
                using (var dialog = new System.Windows.Forms.FolderBrowserDialog())
                {
                    dialog.Description = "AI智建";
                    dialog.ShowNewFolderButton = true;

                    // 设置初始路径（如果已有值）
                    if (!string.IsNullOrEmpty(SavePath))
                    {
                        dialog.SelectedPath = SavePath;
                    }

                    if (dialog.ShowDialog() == System.Windows.Forms.DialogResult.OK)
                    {
                        SavePath = dialog.SelectedPath;
                        SutSpaceName = Path.GetFileName(dialog.SelectedPath);
                    }
                }
            }
            catch
            {

            }
            finally
            {
                IsPopupOpen = false;
            }


        });
        //登录按钮
        public RelayCommand<Window> LoginCommand => new(async (window) =>
        {
            try
            {
                SetMaskVisibilityAction?.Invoke(true);
                Login login = new Login() { Owner = window };
                login.ShowDialog();
                SetMaskVisibilityAction?.Invoke(false);
                var data = await _accountApi.VerifyToken();
                if (data != null)
                {
                    if (data.Code == 200)
                    {
                        NotLoginVisib = Visibility.Collapsed;
                        LoginVisib = Visibility.Visible;
                        UserName = ApiConfig.UserName;
                        AddGroupHistories();
                    }
                }

            }
            catch
            {

            }

        });
        //退出
        public RelayCommand<Window> SignOutCommand => new(async(window) =>
        {

            try
            {  // 网络预检：无法上网则弹窗提示并中止（避免无效请求）
                if (!await NetworkHelper.CheckNetworkAndNotifyAsync()) return;

                _accountApi.SignOut();
                NotLoginVisib = Visibility.Visible;
                LoginVisib = Visibility.Collapsed;
                UserName = string.Empty;
                var info = new SavedLoginInfo
                {
                    AccountToken = string.Empty,
                    UserId = string.Empty,
                    Phone = ApiConfig.Phone,
                    Password = ApiConfig.Password,
                    Authorise = 0,
                    UserName = string.Empty,
                };
                // 序列化为 JSON 明文
                var json = JsonSerializer.Serialize(info);

                // 加密整个 JSON 字符串
                var encrypted = ApiConfig.EncryptString(json);

                // 写入程序同目录
                System.IO.File.WriteAllText(ApiConfig.LoginDataPath, encrypted);
            }
            catch
            {

            }
            finally
            {
                _isUserNamePopupOpen = false;
                OnPropertyChanged(nameof(IsUserNamePopupOpen));

            }

        });
        //检查更新
        public RelayCommand<Window> CheckUpdatesCommand => new( async(window) =>
        {

            try
            {
                // 网络预检：无法上网则弹窗提示并中止（避免无效请求）
                if (!await NetworkHelper.CheckNetworkAndNotifyAsync()) return;
                var isAiUpdata = CheckUpdata.AiConstructionUpdate("AI智建");
                if (!isAiUpdata)
                {
                    MessageBox.Show("暂无更新！", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                    return;
                }
                else
                {
                    Update(isAiUpdata);
                }

            }
            catch
            {

            }
            finally
            {
                _isUserNamePopupOpen = false;
                OnPropertyChanged(nameof(IsUserNamePopupOpen));

            }

        });
        #endregion

        #region 方法
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
                ApiConfig.UserName = info.UserName ?? string.Empty;
                ApiConfig.Phone = info.Phone ?? string.Empty;
                ApiConfig.Password = info.Password ?? string.Empty;
            }
            catch
            {
                // 读取失败静默忽略，不影响正常登录
            }
        }
        //更新
        public async void Update(bool isAiUpdata)
        {
            if (isAiUpdata)
            {


                var isConstruction = CheckUpdata.ConstructionUpdate("报建系列");
                SetMaskVisibilityAction?.Invoke(true);
                WhetherUpdate whetherUpdate = new WhetherUpdate();
                WhetherUpdateVM whetherUpdateVM = new WhetherUpdateVM(isConstruction);
                whetherUpdate.DataContext = whetherUpdateVM;
                whetherUpdate.ShowDialog();
                SetMaskVisibilityAction?.Invoke(false);
                if (whetherUpdateVM.IsUpdate)
                {
                    if (isConstruction)
                    {
                        Process[] processes = Process.GetProcessesByName("Revit");
                        if (processes.Length > 0)
                        {
                            System.Windows.MessageBox.Show("检查到Revit正在运行,请关闭Revit再进行更新！", "提示", MessageBoxButton.OK, MessageBoxImage.Error);
                            return;
                        }
                    }

                    string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                    var updatePath = Path.Combine(baseDir, "AIContstructUpdate.exe");
                    //获取更新程序包
                    var aiUpdatePage = CheckUpdata.GetAiUpdatePage();
                    //压缩包
                    string zipPath = Path.Combine(baseDir, "update.zip");
                    await CheckUpdata.DownloadZipAsync(aiUpdatePage.ApplicationLinks, zipPath);
                    //解压包
                    await CheckUpdata.ExtractWithBatchedProgressAsync(zipPath, baseDir);
                    File.Delete(zipPath);
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = updatePath,
                        UseShellExecute = true
                    });
                    Application.Current.Shutdown();
                }

            }
        }

        /// <summary>
        /// 基于UTC毫秒时间戳 + 随机后缀生成唯一ID
        /// </summary>
        public static string GenerateTimeId()
        {
            long ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            // 0~9999随机数，减少碰撞概率
            int rand = Random.Shared.Next(100000);
            return $"{ms}{rand:D4}";
        }
        //获取对话组数据
        public async void AddGroupHistories()
        {
            try
            {
                UserName = ApiConfig.UserName;
                GroupHistoryNodes.Clear();
                GroupTaskHistories.Clear();
                var result = await _accountApi.SeleteGroupHistory();
                if (result.Code == 200)
                {
                    foreach (var item in result.Rows)
                    {
                        if (string.IsNullOrEmpty(item.SavePath))
                        {
                            GroupTaskHistories.Add(item);
                        }
                    }

                    // 按 SavePath 分组构建树形节点
                    var savePathGroups = result.Rows
                        .Where(x => !string.IsNullOrEmpty(x.SavePath))
                        .GroupBy(x => x.SavePath);

                    foreach (var group in savePathGroups)
                    {
                        var groupName = System.IO.Path.GetFileName(group.Key);
                        GroupHistoryNodes.Add(new GroupHistoryNode
                        {
                            GroupName = string.IsNullOrEmpty(groupName) ? group.Key : groupName,
                            Children = group.ToList(),
                            SavePath = group.Key
                        });
                    }
                }
                if (GroupHistoryNodes.Count > 0)
                {
                    SpaceTitle = "空间" + "(" + GroupHistoryNodes.Count + ")";
                }
                else
                {
                    SpaceTitle = "空间";
                }
                if (GroupTaskHistories.Count > 0)
                {
                    TaskTitle = "任务" + "(" + GroupTaskHistories.Count + ")";
                }
                else
                {
                    TaskTitle = "任务";
                }
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[主页] 加载对话组失败: {ex.Message}");
            }
        }
     
        /// <summary>
        /// 加载选中对话组的历史消息
        /// </summary>
        private async Task LoadChatHistoryAsync(GroupHistory item)
        {
            try
            {
                SavePath = string.Empty;
                LogHelper.Info($"[对话] 切换到对话组: {item.SessionGroupId}, 标题: {item.Title}");

                // 设置为当前对话组ID
                sessionGroupId = item.SessionGroupId;
                if (!string.IsNullOrEmpty(item.SavePath))
                {
                    SavePath = item.SavePath;
                }
                var result = await _accountApi.SelectChatHistory(item.SessionGroupId);
                if (result?.Code != 200 || result.Data == null)
                {
                    LogHelper.Warn($"[对话] 查询历史对话失败: code={result?.Code}, msg={result?.Msg}");
                    return;
                }

                // 清空当前消息列表并填充历史对话
                Messages.Clear();
                _history.Clear();
                //msg.Content 回显数据   msg.Summarize  传入的上下文
                foreach (var msg in result.Data)
                {
                    if (string.IsNullOrWhiteSpace(msg.Content)) continue;

                    var isUser = string.Equals(msg.Role, "user", StringComparison.OrdinalIgnoreCase);
                    Messages.Add(new ChatMessage
                    {
                        IsUser = isUser,
                        Content = msg.Content,
                        IsThinking = false
                    });
                    string content = "";
                    if (msg.Role == "user")
                    {
                        content = msg.Content;
                    }
                    else
                    {
                        // Summarize 是传给 AGENT 的上下文；历史脏数据可能为空，用 Content 兜底，
                        // 避免空 content 的 assistant 消息进入下一轮请求导致报错
                        content = string.IsNullOrWhiteSpace(msg.Summarize) ? msg.Content : msg.Summarize;
                    }
                    // 同步到 API 对话历史
                    _history.Add(new HistoryMessage
                    {
                        Role = msg.Role,
                        Content = content
                    });
                }

                // 显示对话内容区域
                DialogueContent = Visibility.Visible;
                InitializeTitie = Visibility.Collapsed;
                ScrollToEnd();
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[对话] 加载历史对话异常: {ex.Message}");
            }
        }

        #endregion
     
        #region 发送 & 停止命令

        public RelayCommand<TextBox> SendContentCommand => new(async (textbox) =>
        {

            // 网络预检：无法上网则弹窗提示并中止（避免无效请求）
            if (!await NetworkHelper.CheckNetworkAndNotifyAsync()) return;

            if (ApiConfig.Authorise == 2 || ApiConfig.Authorise == 3)
            {
                var authorization = await _accountApi.SelectAuthorization();
                ApiConfig.Authorise = authorization.data;
                if (authorization.data != 1)
                {
                    if (MessageBox.Show("该账号未申请授权,是否申请授权？", "提示", MessageBoxButton.OKCancel, MessageBoxImage.Information) == MessageBoxResult.OK)
                    {
                        await _accountApi.AddAuthorization();
                   
                        return;
                    }
                }
            }
            if (ApiConfig.Authorise == 0)
            {
                MessageBox.Show("该账号授权审核当中,请联系客服进行审核！", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }
            if (string.IsNullOrWhiteSpace(SendContent)) return;

            var userMessage = SendContent.Trim();
            SendContent = string.Empty;

            LogHelper.Info($"[对话] 用户发送消息 (len={userMessage.Length}): {userMessage[..Math.Min(userMessage.Length, 80)]}");
            //初始化进来生成对话组ID
            if (string.IsNullOrEmpty(sessionGroupId))
            {
                sessionGroupId = GenerateTimeId();
            }
            // 切换到对话视图
            DialogueContent = Visibility.Visible;
            InitializeTitie = Visibility.Collapsed;

            // Runtime 未就绪 → 提示等待
            if (!App.Runtime.IsReady)
            {
                // 先显示用户消息
                Messages.Add(new ChatMessage { IsUser = true, Content = userMessage });

                Messages.Add(new ChatMessage
                {
                    IsUser = false,
                    Content = "⏳ AI 引擎正在启动中，请稍候再试...",
                    IsThinking = false
                });
                ScrollToEnd();
                return;
            }

            // 取消上一轮任务
            CancelCurrentRun();

            _runCts = new CancellationTokenSource();
            var ct = _runCts.Token;

            IsLoading = true;

            // ====== 添加用户气泡（右侧） ======
            var userBubble = new ChatMessage { IsUser = true, Content = userMessage };
            Messages.Add(userBubble);

            // ====== 添加 AI 初始思考气泡（左侧） ======
            var aiBubble = new ChatMessage
            {
                IsUser = false,
                Content = string.Empty,
                IsThinking = true,
                ThinkingText = "AI 正在思考"
            };
            Messages.Add(aiBubble);


            // 当前活跃的 AI 文本气泡（用于接收 assistant_message）
            currentAiTextBubble = aiBubble;

            // 记录 AI 回复内容（用于写入历史）
            var aiFullContent = string.Empty;
            // 仅持久化最终答复。计划/推理事件用于 UI 展示，不能作为下一轮模型上下文。
            finalAssistantContent = string.Empty;

            // Runtime 进程退出标记（用于区分用户取消 vs 进程崩溃）
            var runtimeExited = false;
            EventHandler? onRuntimeExited = null;

            try
            {

                // 1. 创建 Agent Run
                var request = new CreateRunRequest();
                if (string.IsNullOrEmpty(SavePath))
                {
                     request = new CreateRunRequest
                    {
                        RequestId = $"req-{DateTime.Now:yyyyMMddHHmmss}-{Guid.NewGuid():N}"[..8],
                        ConversationId = _conversationId,
                        UserMessage = userMessage,
                        History = _history.Count > 0 ? new List<HistoryMessage>(_history) : null,
                       
                    };
                }
                else
                {
                    request = new CreateRunRequest
                    {
                        RequestId = $"req-{DateTime.Now:yyyyMMddHHmmss}-{Guid.NewGuid():N}"[..8],
                        ConversationId = _conversationId,
                        UserMessage = userMessage,
                        History = _history.Count > 0 ? new List<HistoryMessage>(_history) : null,
                        Attachments=new List<object>()
                        {
                            new { path=SavePath, type="revit_model"}
                        }
                    };
                }
                createResponse = await ApiClient.CreateRunAsync(request, ct);
                if (_history.Count == 0)
                {
                    //生成标题 
                    var createTitie = await ApiClient.CreateTitie("user", userMessage);
                    //添加对话组历史
                    await _accountApi.AddGrouprecord(sessionGroupId, createTitie.Topic, savePath);
                    DialogueTitle = createTitie.Topic;
                    AddGroupHistories();
                }
                if (createResponse == null)
                {
                    LogHelper.Error("[对话] CreateRunAsync 返回 null");
                    currentAiTextBubble.IsThinking = false;
                    currentAiTextBubble.Content = "> ⚠️ 创建任务失败，请检查后端服务是否正常运行。";
                    ScrollToEnd();
                    return;
                }

                // API 成功 → 记录用户消息到历史
                _history.Add(new HistoryMessage { Role = "user", Content = userMessage });

                _currentRunId = createResponse.RunId;
                LogHelper.Info($"[对话] Run 已创建: {_currentRunId}, status: {createResponse.Status}");

                // 订阅 Runtime 进程退出事件，进程崩溃时主动取消 SSE 读取
                onRuntimeExited = (_, _) =>
                {
                    runtimeExited = true;
                    LogHelper.Warn("[对话] Runtime 进程已退出，取消当前 SSE 读取");
                    _runCts?.Cancel();
                };
                App.Runtime.ProcessExited += onRuntimeExited;

                try
                {
                    var msgid = GenerateTimeId();
                    //增加对话历史用户输入
                    await _accountApi.AddDialogueHistory(createResponse.RunId, sessionGroupId, msgid, "user", userMessage, string.Empty);
                    // 2. SSE 长连接订阅事件
                    await foreach (var sseEvent in ApiClient.SubscribeToEventsAsync(_currentRunId, ct))
                    {
                        // run_completed 的 final_answer 与 assistant_message(phase=final) 内容重复，只记录摘要
                        if (sseEvent.EventType == "run_completed")
                            LogHelper.Info($"[SSE] event=run_completed, data=(omitted, same as assistant_message)");
                        else
                            LogHelper.Info($"[SSE] event={sseEvent.EventType}, data={sseEvent.Data}");
                        switch (sseEvent.EventType)
                        {
                            case "assistant_message":
                                // 确保有活跃的 AI 文本气泡
                                if (currentAiTextBubble == null)
                                {
                                    currentAiTextBubble = new ChatMessage
                                    {
                                        IsUser = false,
                                        Content = string.Empty,
                                        IsThinking = false
                                    };
                                    Messages.Add(currentAiTextBubble);
                                }

                                currentAiTextBubble.IsThinking = false;
                                var assistantEvent = ParseAssistantMessage(sseEvent.Data);
                                var chunk = assistantEvent?.Content ?? string.Empty;
                                if (!string.IsNullOrEmpty(chunk))
                                {
                                    aiFullContent += chunk;
                                    await AppendChunkToBubbleAsync(currentAiTextBubble, chunk, ct);
                                }
                                break;

                            case "run_completed":
                                HandleRunCompleted(currentAiTextBubble, sseEvent.Data);
                                finalAssistantContent = ParseRunCompletedAnswer(sseEvent.Data) ?? finalAssistantContent;
                                break;

                            case "run_failed":
                                if (currentAiTextBubble != null)
                                {
                                    currentAiTextBubble.IsThinking = false;
                                    HandleRunFailed(currentAiTextBubble, sseEvent.Data);
                                }
                                break;

                            case "run_cancelled":
                                if (currentAiTextBubble != null)
                                {
                                    currentAiTextBubble.IsThinking = false;
                                    currentAiTextBubble.Content += "\n\n> ⚠️ 任务已被取消。";
                                    ScrollToEnd();
                                }
                                break;

                            case "run_queued":
                                if (currentAiTextBubble != null)
                                    currentAiTextBubble.ThinkingText = "⏳ 任务已加入队列...";
                                break;

                            case "run_started":
                                if (currentAiTextBubble != null)
                                    currentAiTextBubble.ThinkingText = "AI 正在思考";
                                break;

                            case "tool_started":
                                // 创建新的可折叠步骤并加入列表
                                if (currentAiTextBubble != null)
                                {
                                    var toolInfo = ParseToolEvent(sseEvent.Data);
                                    if (toolInfo != null)
                                    {
                                        var desc = !string.IsNullOrEmpty(toolInfo.Summary)
                                            ? toolInfo.Summary
                                            : $"正在执行 {toolInfo.Tool}";

                                        var step = new ToolStep
                                        {
                                            Title = desc,
                                            Icon = "🔧",
                                            IsSuccess = false,
                                            IsCompleted = false,
                                            StepNumber = currentAiTextBubble.Steps.Count + 1
                                        };
                                        currentAiTextBubble.Steps.Add(step);

                                        // 首次工具调用时记录开始时间
                                        if (currentAiTextBubble.StepsStartTime == null)
                                            currentAiTextBubble.StepsStartTime = DateTime.Now;
                                    }
                                    // 工具执行期间显示"AI 正在思考..."
                                    currentAiTextBubble.IsThinking = true;
                                    currentAiTextBubble.ThinkingText = "AI 正在思考";
                                    ScrollToEnd();
                                }
                                break;

                            case "tool_completed":
                                // 更新最后一步的状态和详细描述
                                if (currentAiTextBubble != null)
                                {
                                    var toolInfo = ParseToolEvent(sseEvent.Data);
                                    if (toolInfo != null && currentAiTextBubble.Steps.Count > 0)
                                    {
                                        var lastStep = currentAiTextBubble.Steps[currentAiTextBubble.Steps.Count - 1];
                                        lastStep.IsSuccess = toolInfo.Success == true;
                                        lastStep.IsCompleted = true;
                                        lastStep.Icon = toolInfo.Success == true ? "✅" : "❌";
                                        lastStep.Content = !string.IsNullOrEmpty(toolInfo.Summary)
                                            ? toolInfo.Summary
                                            : $"{toolInfo.Tool} 执行{(toolInfo.Success == true ? "成功" : "失败")}";

                                        // 更新折叠头为已完成+耗时
                                        currentAiTextBubble.StepsHeaderText =
                                            $"已完成 {FormatElapsed(currentAiTextBubble.StepsStartTime)}";
                                        ScrollToEnd();
                                    }
                                }
                                break;
                        }
                    }

                    // 记录 AI 回复到历史（AgentRuntime 上下文，仅最终总结）
                    if (!string.IsNullOrWhiteSpace(finalAssistantContent))
                    {
                        _history.Add(new HistoryMessage { Role = "assistant", Content = finalAssistantContent });
                    }

                    msgid = GenerateTimeId();
                    // 增加对话历史 AI 回复：保存用户实际看到的完整气泡内容
                    // 与 _history 区分：_history 给 AgentRuntime 做上下文，只需 final_answer；
                    // AddDialogueHistory 给后台做聊天记录，需要保留用户看到的完整文本。
                    var displayedContent = !string.IsNullOrWhiteSpace(currentAiTextBubble?.Content)
                        ? CleanContentSteps(currentAiTextBubble.Content)
                        : finalAssistantContent;
                    await _accountApi.AddDialogueHistory(createResponse.RunId, sessionGroupId, msgid, "assistant", displayedContent, finalAssistantContent);
                }
                finally
                {
                    App.Runtime.ProcessExited -= onRuntimeExited;
                }
            }
            catch (OperationCanceledException)
            {
                if (runtimeExited)
                {
                    LogHelper.Warn("[对话] 任务因 Runtime 进程退出而中断");
                    if (currentAiTextBubble != null)
                    {
                        currentAiTextBubble.IsThinking = false;
                        if (string.IsNullOrEmpty(currentAiTextBubble.Content))
                            currentAiTextBubble.Content = "> ⚠️ AI 引擎意外停止，请重启应用后重试。";
                    }
                }
                else
                {
                    LogHelper.Info("[对话] 任务被用户取消");
                }
            }
            catch (HttpRequestException ex)
            {
                LogHelper.Error($"[对话] 网络请求失败: {ex.Message}");
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[对话] 未处理异常: {ex.Message}");
            }
            finally
            {
                LogHelper.Info($"[对话] Run 结束: {_currentRunId}, 回复长度={aiFullContent.Length}");
                if (currentAiTextBubble != null)
                {
                    // 最终清理正文中的步骤描述
                    currentAiTextBubble.Content = CleanContentSteps(currentAiTextBubble.Content);
                    currentAiTextBubble.IsThinking = false;
                    if (currentAiTextBubble.Steps.Count > 0)
                    {
                        currentAiTextBubble.StepsHeaderText =
                            $"已完成 {FormatElapsed(currentAiTextBubble.StepsStartTime)}";
                    }
                }
                IsLoading = false;
                _currentRunId = null;
            }
        });

        public RelayCommand StopCommand => new(async () =>
        {
            if (_currentRunId != null)
            {
                try
                {
                    if (_apiClient != null)
                        await _apiClient.CancelRunAsync(_currentRunId);
                    var msgid = GenerateTimeId();
                    // 增加对话历史ai回复：保存用户实际看到的完整气泡内容
                    // 与 SendContentCommand 正常结束逻辑一致，使用气泡内容而非仅 finalAssistantContent
                    var stopDisplayedContent = !string.IsNullOrWhiteSpace(currentAiTextBubble?.Content)
                        ? CleanContentSteps(currentAiTextBubble.Content)
                        : finalAssistantContent;

                    // 写入后台历史。summarize（传给 AGENT 的上下文）以 finalAssistantContent 为主，
                    // 为空时用实际展示内容兜底，避免重载历史后 assistant 上下文为空导致后续请求报错
                    if (!string.IsNullOrWhiteSpace(stopDisplayedContent) || !string.IsNullOrWhiteSpace(finalAssistantContent))
                    {
                        await _accountApi.AddDialogueHistory(_currentRunId, sessionGroupId, msgid, "assistant",
                            stopDisplayedContent,
                            !string.IsNullOrWhiteSpace(finalAssistantContent) ? finalAssistantContent : stopDisplayedContent);
                    }
                }
                catch (Exception ex)
                {
                    LogHelper.Error($"[对话] 取消 Run 失败: {ex.Message}");
                }
            }
            CancelCurrentRun();
        });

        #endregion

        #region SSE 事件处理

        /// <summary>
        /// 将新内容追加到 AI 气泡已有内容之后，仅对新部分做逐字打字动画
        /// </summary>
        /// <summary>匹配步骤描述前缀，如 "1. 正在执行 str_replace_editor。"</summary>
        private static readonly Regex StepDescPattern = new(
            @"\d+\.\s*正在执行\s+\w+\s*[。:：]\s*",
            RegexOptions.Compiled);

        /// <summary>匹配完整的步骤描述行</summary>
        private static readonly Regex StepDescLinePattern = new(
            @"^\s*\d+\.\s*正在执行\s+\w+\s*[。:：][^\n]*\n?",
            RegexOptions.Compiled | RegexOptions.Multiline);

        /// <summary>匹配行内代码，转换为加粗文本以便当做正文复制</summary>
        private static readonly Regex InlineCodePattern = new(
            @"(?<![`\\])`([^`\n]+)`(?![`\\])",
            RegexOptions.Compiled);

        /// <summary>过滤 chunk 中的步骤描述前缀</summary>
        private static string CleanStepChunk(string text)
        {
            if (string.IsNullOrEmpty(text)) return text;
            return StepDescPattern.Replace(text, "");
        }

        /// <summary>最终清理 Content 中的步骤描述行</summary>
        private static string CleanContentSteps(string content)
        {
            if (string.IsNullOrEmpty(content)) return content;
            var cleaned = StepDescLinePattern.Replace(content, "");
            cleaned = InlineCodePattern.Replace(cleaned, "**$1**");
            cleaned = Regex.Replace(cleaned, @"\n{3,}", "\n\n");
            return cleaned.Trim();
        }

        /// <summary>将行内代码替换为加粗文本，使其可像正文一样复制</summary>
        private static string InlineCodeToBold(string text)
        {
            if (string.IsNullOrEmpty(text)) return text;
            return InlineCodePattern.Replace(text, "**$1**");
        }

        private async Task AppendChunkToBubbleAsync(ChatMessage bubble, string chunk, CancellationToken ct)
        {
            // 实时过滤步骤描述，避免思考过程混入正文
            chunk = CleanStepChunk(chunk);
            if (string.IsNullOrEmpty(chunk)) return;

            var existing = bubble.Content ?? string.Empty;
            var totalLen = chunk.Length;
            var batchSize = totalLen > 2000 ? 10 : 3;
            var delayMs = totalLen > 2000 ? 8 : 20;

            var typed = string.Empty;
            for (int i = 0; i < totalLen; i += batchSize)
            {
                ct.ThrowIfCancellationRequested();

                var count = Math.Min(batchSize, totalLen - i);
                typed += chunk.Substring(i, count);

                var display = InlineCodeToBold(existing + typed);
                await Application.Current.Dispatcher.InvokeAsync(() =>
                {
                    bubble.Content = display;
                    ScrollToEnd();
                }, DispatcherPriority.Normal, ct);

                await Task.Delay(delayMs, ct);
            }
        }

        private static AssistantMessageEvent? ParseAssistantMessage(string data)
        {
            try
            {
                var msg = JsonSerializer.Deserialize<AssistantMessageEvent>(data);
                if (!string.IsNullOrEmpty(msg?.Content))
                    return msg;
            }
            catch (JsonException) { }
            return null;
        }

        private static string? ParseRunCompletedAnswer(string data)
        {
            try
            {
                return JsonSerializer.Deserialize<RunCompletedEvent>(data)?.FinalAnswer;
            }
            catch (JsonException)
            {
                return null;
            }
        }

        /// <summary>
        /// 解析 tool_started / tool_completed 事件中的工具信息
        /// </summary>
        private static ToolEvent? ParseToolEvent(string data)
        {
            try
            {
                return JsonSerializer.Deserialize<ToolEvent>(data);
            }
            catch
            {
                return null;
            }
        }

        /// <summary>格式化已用时间（如 "3s"、"1m35s"）</summary>
        private static string FormatElapsed(DateTime? startTime)
        {
            if (startTime == null) return "0s";
            var elapsed = DateTime.Now - startTime.Value;
            if (elapsed.TotalMinutes >= 1)
                return $"{(int)elapsed.TotalMinutes}m{elapsed.Seconds}s";
            return $"{elapsed.Seconds}s";
        }

        private void HandleRunCompleted(ChatMessage? bubble, string data)
        {
            if (bubble != null)
            {
                bubble.IsThinking = false;
                if (bubble.Steps.Count > 0)
                {
                    bubble.StepsHeaderText =
                        $"已完成 {FormatElapsed(bubble.StepsStartTime)}";
                }
            }

            try
            {
                var completed = JsonSerializer.Deserialize<RunCompletedEvent>(data);

                // 如果 assistant_message 没推送内容，兜底用 final_answer
                if (completed != null && !string.IsNullOrEmpty(completed.FinalAnswer))
                {
                    if (bubble != null && string.IsNullOrEmpty(bubble.Content))
                    {
                        Application.Current.Dispatcher.Invoke(() =>
                        {
                            bubble.Content = completed.FinalAnswer;
                            ScrollToEnd();
                        });
                    }
                }
            }
            catch { }
        }

        private void HandleRunFailed(ChatMessage bubble, string data)
        {
            try
            {
                var failed = JsonSerializer.Deserialize<StatusEvent>(data);
                var msg = failed?.Message ?? "未知错误";
                LogHelper.Error($"[对话] Run 执行失败: {msg}");
                Application.Current.Dispatcher.Invoke(() =>
                {
                    bubble.Content += $"\n\n> ❌ 任务执行失败: {msg}";
                    ScrollToEnd();
                });
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[对话] 解析 run_failed 事件失败: {ex.Message}, raw: {data}");
                Application.Current.Dispatcher.Invoke(() =>
                {
                    bubble.Content += "\n\n> ❌ 任务执行失败。";
                    ScrollToEnd();
                });
            }
        }

        #endregion

        #region UI 辅助

        /// <summary>滚动到聊天底部</summary>
        private void ScrollToEnd()
        {
            Application.Current.Dispatcher.BeginInvoke(() =>
            {
                if (ScrollViewer == null) return;
                ScrollViewer.UpdateLayout();
                ScrollViewer.ScrollToEnd();
            }, DispatcherPriority.Background);
        }

        #endregion

        #region 清理

        private void CancelCurrentRun()
        {
            if (_runCts != null)
            {
                _runCts.Cancel();
                _runCts.Dispose();
                _runCts = null;
            }
        }

        #endregion
    }
}
