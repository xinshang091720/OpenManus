using System;
using System.Net.Http;
using System.Net.NetworkInformation;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
// 消歧：HandyControl 与 System.Windows 都有 MessageBox，此处明确指 HandyControl 风格弹窗
using HcMessageBox = HandyControl.Controls.MessageBox;

namespace AiConstruction.Services
{
    /// <summary>
    /// 外网连通性检查 —— 判定本机是否能上网（独立 helper，不影响既有代码）
    /// 判定标准：1.5 秒内收到任意 HTTP 响应（200/302/403/500 都算）即视为联网成功，
    /// 因为能拿到响应头说明 DNS + TCP + TLS 链路已通，本机网络是好的。
    /// 只有 DNS 解析失败 / TCP 超时 / TLS 握手失败（即完全无法出网）才判定为断网。
    /// 每次点击按钮都真实探测，没网络就弹窗提示；后台自动重试（无需用户操作），
    /// 网络恢复后自动提示"已恢复"。
    /// </summary>
    public static class NetworkHelper
    {
        // 探测地址：国内最稳定的外网地址（DNS 与服务器均在境内，结果可靠）
        private const string ProbeUrl = "https://www.baidu.com";

        // 连接超时（秒）：配合网卡预检，断网场景最快毫秒级返回；1.5 秒足够完成一次探测
        private const double TimeoutSeconds = 0.5;

        // 断网后的自动重试间隔（毫秒）
        private const int RetryIntervalMs = 3000;

        private static readonly HttpClient _client = new()
        {
            Timeout = TimeSpan.FromSeconds(TimeoutSeconds)
        };

        /// <summary>
        /// 探测外网连通性：收到任意 HTTP 响应头即视为通（每次调用都真实探测）
        /// </summary>
        /// <returns>true = 本机可以上网；false = 无法连接外网</returns>
        public static async Task<bool> CheckInternetAsync(CancellationToken ct = default)
        {
            // 网卡预检：本机没有启用的网络接口（网线拔了/WiFi 关闭/网卡禁用）→ 毫秒级秒回，不用等 HTTP 超时
            if (!NetworkInterface.GetIsNetworkAvailable())
                return false;

            try
            {
                // ResponseHeadersRead：拿到响应头即返回，不必等完整 body，更快
                using var resp = await _client.GetAsync(
                    ProbeUrl,
                    HttpCompletionOption.ResponseHeadersRead,
                    ct);
                return true;
            }
            catch (OperationCanceledException)
            {
                // 超时或外部取消 → 无法出网
                return false;
            }
            catch
            {
                // DNS 失败 / 连接被拒 / TLS 异常等 → 无法出网
                return false;
            }
        }

        // 防重入标志：检测过程中 Timer 回调不再发起新检测，避免并发检测
        private static volatile bool _checking;

        // 进行中的检测任务：并发调用（启动检测 vs 按钮点击）复用同一任务，保证结果一致，
        // 不会出现"检测未出结果就误放行"的情况
        private static Task<bool>? _pendingCheck;
        private static readonly object _pendingLock = new();

        // 离线状态标志：标记当前处于离线态（用于网络恢复时提示）。
        // 注意：断网期间按钮每次调用仍会弹窗，此标志仅控制"恢复提示"与日志，不抑制按钮弹窗。
        private static volatile bool _offline;

        // 自动重试定时器：断网后无需用户点击，后台定时检测，网络恢复自动提示
        private static Timer? _retryTimer;
        private static readonly object _retryLock = new();

        /// <summary>
        /// 检测外网连通性并提示（启动时与关键按钮共用）：
        /// 网络通 → 返回 true；网络不通 → 每次调用都弹窗提示（启动一次、按钮每点一次弹一次）
        /// + 后台自动重试（无需用户点"确定"），返回 false。
        /// 自动重试在后台静默进行，网络恢复时自动弹"网络已恢复"，不会重复弹断网窗。
        /// 启动场景：_ = NetworkHelper.CheckNetworkAndNotifyAsync();（忽略返回值）
        /// 按钮场景：if (!await NetworkHelper.CheckNetworkAndNotifyAsync()) return; // 网络不通则中止操作
        /// </summary>
        public static Task<bool> CheckNetworkAndNotifyAsync()
        {
            lock (_pendingLock)
            {
                // 防重入：检测进行中则复用同一任务，并发调用拿到相同结果（不会误放行）
                if (_pendingCheck != null) return _pendingCheck;

                var task = CheckCoreAsync();
                _pendingCheck = task;
                // 任务结束后清空引用，下次调用重新检测
                _ = task.ContinueWith(_ =>
                {
                    lock (_pendingLock) _pendingCheck = null;
                }, TaskScheduler.Default);
                return task;
            }
        }

        /// <summary>检测核心逻辑：探测外网 + 弹窗提示 + 自动重试 + 恢复提示</summary>
        private static async Task<bool> CheckCoreAsync()
        {
            _checking = true;
            try
            {
                // 检测通过 → 停止自动重试；若此前处于离线态，提示恢复
                if (await CheckInternetAsync())
                {
                    StopAutoRetry();
                    if (_offline)
                    {
                        _offline = false;
                        NotifyOnUi("网络已恢复，可以继续操作。", isWarning: false);
                        LogHelper.Info("[网络] 外网连接已恢复");
                    }
                    return true;
                }

                // 断网：每次调用（启动/点击按钮）都弹窗提示，同时后台自动重试（无需用户操作）。
                // _offline 仅用于：首次离线记日志、网络恢复时弹"已恢复"，不抑制按钮弹窗。
                if (!_offline)
                {
                    _offline = true;
                    LogHelper.Warn("[网络] 外网连接失败，无法上网");
                }
                NotifyOnUi("网络连接失败，正在自动重试，请检查网络连接。", isWarning: true);
                StartAutoRetry();
                return false;
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[网络] 检测异常: {ex}");
                return false;
            }
            finally
            {
                _checking = false;
            }
        }

        /// <summary>在 UI 线程弹提示（断网警告 / 恢复通知），非阻塞式封送，不阻塞调用方</summary>
        private static void NotifyOnUi(string message, bool isWarning)
        {
            var dispatcher = Application.Current?.Dispatcher;
            if (dispatcher == null)
            {
                // 极端情况（无 Application 上下文）直接弹窗
                ShowBox(message, isWarning);
                return;
            }
            dispatcher.BeginInvoke(new Action(() => ShowBox(message, isWarning)));
        }

        private static void ShowBox(string message, bool isWarning)
        {
            MessageBox.Show(message, "网络提示", MessageBoxButton.OK,
                isWarning ? MessageBoxImage.Warning : MessageBoxImage.Information);
        }

        /// <summary>启动后台自动重试定时器（幂等：已在跑则忽略）</summary>
        private static void StartAutoRetry()
        {
            lock (_retryLock)
            {
                if (_retryTimer != null) return;
                _retryTimer = new Timer(OnRetryTick, null, RetryIntervalMs, RetryIntervalMs);
            }
        }

        /// <summary>停止自动重试定时器（幂等）</summary>
        private static void StopAutoRetry()
        {
            lock (_retryLock)
            {
                _retryTimer?.Dispose();
                _retryTimer = null;
            }
        }

        /// <summary>自动重试回调：网络恢复则停止重试并提示；仍断网则静默等待下一轮（不重复弹窗）</summary>
        private static void OnRetryTick(object? state)
        {
            if (_checking) return;
            _checking = true;
            try
            {
                if (CheckInternetAsync().GetAwaiter().GetResult())
                {
                    StopAutoRetry();
                    _offline = false;
                    NotifyOnUi("网络已恢复，可以继续操作。", isWarning: false);
                    LogHelper.Info("[网络] 外网连接已恢复");
                }
            }
            catch (Exception ex)
            {
                LogHelper.Error($"[网络] 自动重试异常: {ex}");
            }
            finally
            {
                _checking = false;
            }
        }
    }
}
