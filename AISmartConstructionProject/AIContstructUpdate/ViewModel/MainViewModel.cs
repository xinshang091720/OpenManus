using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Xml.Linq;

namespace AIContstructUpdate.ViewModel
{
    public class MainViewModel : ObservableObject
    {
        public MainWindow MainWindowView { get; set; }
        // 本地更新地址配置文件路径
        private static readonly string _linksConfigPath = Path.Combine(
            AppDomain.CurrentDomain.BaseDirectory, "update_links.json");

        //进度条值
        private double progressjd;
        public double Progressjd
        {
            get => progressjd;
            set => SetProperty(ref progressjd, value);
        }

        private string progressNumerical = string.Empty;
        /// <summary>进度数字，如 "99%"、"100%"、"0%"</summary>
        public string ProgressNumerical
        {
            get => progressNumerical;
            set => SetProperty(ref progressNumerical, value);
        }

        private string statusText = string.Empty;
        /// <summary>阶段状态文字，如 "正在解压..."、"解压完成"、"正在复制..."</summary>
        public string StatusText
        {
            get => statusText;
            set => SetProperty(ref statusText, value);
        }
        private ObservableCollection<UpdateLogItem>? _updateLogItems;

        public ObservableCollection<UpdateLogItem> UpdateLogItems
        {
            get
            {
                // 只在字段null的时候初始化一次，不要每次get都new
                if (_updateLogItems == null)
                {
                    _updateLogItems = new ObservableCollection<UpdateLogItem>();
                }
                return _updateLogItems;
            }
            set => SetProperty(ref _updateLogItems, value);
        }

        public MainViewModel(MainWindow mainWindow)
        {

            MainWindowView = mainWindow;
            mainWindow.Loaded += MainWindow_Loaded;
        }

        /// <summary>
        /// 强制结束指定进程，避免更新时文件被占用
        /// </summary>
        private static void KillProcessIfRunning(string processName)
        {
            var procs = System.Diagnostics.Process.GetProcessesByName(processName);
            foreach (var p in procs)
            {
                try
                {
                    p.Kill();
                    p.WaitForExit(5000);
                }
                catch
                {
                    // 进程可能已经退出或无权限，忽略
                }
            }
        }

        private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
        {
            try
            {


                //// 更新前先关闭目标进程，避免文件占用导致更新失败
                KillProcessIfRunning("BeeSync.AgentRuntime");
                KillProcessIfRunning("AiConstruction");
                var config = LoadLinksConfig();

                if (!string.IsNullOrEmpty(config.AiConstructionLink))
                {
                    Reamrke(config.AiRemark);
                    StatusText = "正在更新主程序...";
                    await UpdateAi(config);
                }
                if (!string.IsNullOrEmpty(config.ConstructionLink))
                {
                    KillProcessIfRunning("Revit");
                    Reamrke(config.ConstructionRemark);
                    StatusText = "正在更新插件...";
                    await UpdatePlugin(config);
                }

                // 全部阶段结束后强制置 100% 并提示，避免最后一个阶段
                // 因为浮点截断永远停在 99%。
                Progressjd = 100;
                ProgressNumerical = "100%";
                StatusText = "更新完成";
                MessageBox.Show("更新完成！", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
                MainWindowView.Close();
            }
            catch (Exception ex)
            {
                // 失败时同样把进度推到 100%，把状态文字切到"更新失败"，
                // 这样用户至少能看到明确收尾，不会再卡在 99%。
                Progressjd = 100;
                ProgressNumerical = "100%";
                StatusText = "更新失败";
                MessageBox.Show(ex.Message, "错误", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }

        public void Reamrke(string content)
        {
            UpdateLogItems.Clear();
            if (!string.IsNullOrEmpty(content))
            {
                var remarks = content.Split('#', StringSplitOptions.RemoveEmptyEntries);
                if (remarks.Length > 0)
                {
                    foreach (var remark in remarks)
                    {
                        UpdateLogItems.Add(new UpdateLogItem() { Content = remark });
                    }
                }
            }
        }

        /// <summary>
        /// 下载文件并实时更新总进度条（流式写入，不占大内存）。
        /// basePct / rangePct 将下载子进度 0-100 映射到总进度条 [basePct, basePct+rangePct]。
        /// </summary>
        private async Task DownloadWithProgressAsync(string url, string filePath, int basePct, int rangePct)
        {
            // 419 MB 的大文件默认 100s 超时很容易被打断（限速/慢网络），
            // 这里显式延长到 10 分钟，足以覆盖绝大多数下载场景。
            using var client = new HttpClient { Timeout = TimeSpan.FromMinutes(10) };

            using var response = await client.GetAsync(url, HttpCompletionOption.ResponseHeadersRead);
            response.EnsureSuccessStatusCode();
            long totalBytes = response.Content.Headers.ContentLength ?? -1L;

            using var stream = await response.Content.ReadAsStreamAsync();
            using var fileStream = new FileStream(filePath, FileMode.Create, FileAccess.Write, FileShare.None, 81920, true);

            var buffer = new byte[81920];
            int bytesRead;
            long totalRead = 0;

            while ((bytesRead = await stream.ReadAsync(buffer, 0, buffer.Length)) > 0)
            {
                await fileStream.WriteAsync(buffer, 0, bytesRead);
                totalRead += bytesRead;

                if (totalBytes > 0)
                {
                    int localPct = (int)(totalRead * 100L / totalBytes);
                    if (localPct > 99) localPct = 99;
                    int globalPct = basePct + localPct * rangePct / 100;
                    Progressjd = globalPct;
                    ProgressNumerical = $"{globalPct}%";
                }
                else
                {
                    ProgressNumerical = $"{totalRead / 1024 / 1024:F1} MB";
                }
            }

            await fileStream.FlushAsync();
            Progressjd = basePct + rangePct;
            ProgressNumerical = $"{basePct + rangePct}%";
        }

        /// <summary>
        /// 后台线程解压 ZIP，每 batchSize 个文件通过 Dispatcher 更新一次总进度。
        /// 既避免了逐文件 Task.Yield 的性能开销，又能让进度条持续推进（不会卡死）。
        /// </summary>
        private async Task ExtractWithBatchedProgressAsync(string zipPath, string destDir, int basePct, int rangePct)
        {
            await Task.Run(() =>
            {
                using var archive = ZipFile.OpenRead(zipPath);
                int total = archive.Entries.Count;
                int done = 0;
                int batchSize = Math.Max(1, total / 40); // 约 40 次 UI 更新

                foreach (var entry in archive.Entries)
                {
                    string filePath = entry.FullName;
                    if (entry.FullName== "SwarmBIM_Application/")
                    {
                        continue;
                    }
                    if (filePath.Contains("SwarmBIM_Application"))
                    {
                        filePath = filePath.Replace("SwarmBIM_Application/", "");
                    }
                    if (Path.GetFileName(entry.FullName)== "AIContstructUpdate.exe")
                    {
                        
                        continue;
                    }
                    string destPath = Path.Combine(destDir, filePath);
                    if (filePath.EndsWith('/') || filePath.EndsWith('\\'))
                    {
                        Directory.CreateDirectory(destPath);
                    }
                    else
                    {
                        string? parentDir = Path.GetDirectoryName(destPath);
                        if (!string.IsNullOrEmpty(parentDir))
                            Directory.CreateDirectory(parentDir);
                        entry.ExtractToFile(destPath, true);
                    }

                    done++;
                    if (done % batchSize == 0 || done == total)
                    {
                        int localPct = total > 0 ? done * 99 / total : 99;
                        int globalPct = basePct + localPct * rangePct / 100;
                        var d = done; var t = total; // 捕获供闭包使用
                        Application.Current.Dispatcher.Invoke(() =>
                        {
                            Progressjd = globalPct;
                            ProgressNumerical = $"{globalPct}%";
                            StatusText = $"正在解压... ({d}/{t})";
                        });
                    }
                }
            });

            Progressjd = basePct + rangePct;
            ProgressNumerical = $"{basePct + rangePct}%";
            StatusText = "解压完成";
        }

        /// <summary>
        /// 后台线程复制文件，每 batchSize 个文件通过 Dispatcher 更新一次总进度。
        /// </summary>
        private async Task CopyWithBatchedProgressAsync(string sourceDir, string targetDir, int basePct, int rangePct)
        {
            await Task.Run(() =>
            {
                var allFiles = Directory.GetFiles(sourceDir, "*", SearchOption.AllDirectories);
                int total = allFiles.Length;
                int done = 0;
                int batchSize = Math.Max(1, total / 10);
                foreach (string file in allFiles)
                {
                    string fileName = Path.GetFileName(file);
                    if (fileName == "AIContstructUpdate.exe")
                    {
                        continue;
                    }
                    string relativePath = file.Substring(sourceDir.Length).TrimStart('\\', '/');
                    string targetPath = Path.Combine(targetDir, relativePath);
                    string? parentDir = Path.GetDirectoryName(targetPath);
                    if (!string.IsNullOrEmpty(parentDir) && !Directory.Exists(parentDir))
                        Directory.CreateDirectory(parentDir);
                    File.Copy(file, targetPath, true);

                    done++;
                    if (done % batchSize == 0 || done == total)
                    {
                        int localPct = total > 0 ? done * 99 / total : 99;
                        int globalPct = basePct + localPct * rangePct / 100;
                        var d = done; var t = total;
                        Application.Current.Dispatcher.BeginInvoke(() =>
                        {
                            Progressjd = globalPct;
                            ProgressNumerical = $"{globalPct}%";
                            StatusText = $"正在复制... ({d}/{t})";
                        });
                    }
                }
            });

            Progressjd = basePct + rangePct;
            ProgressNumerical = $"{basePct + rangePct}%";
            StatusText = "复制完成";
        }

        /// <summary>
        /// 更新主程序：下载(0→55) → 解压(55→82) → 复制文件(82→100)
        /// 解压和复制在后台线程执行，批量更新 UI，进度条真实推进。
        /// </summary>
        public async Task UpdateAi(UpdateLinksConfig config)
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string zipPath = Path.Combine(baseDir, "update.zip");

            // 1. 下载：总进度 0 → 55
            StatusText = "正在下载主程序...";
            Progressjd = 0;
            ProgressNumerical = "0%";
            await DownloadWithProgressAsync(config.AiConstructionLink, zipPath, basePct: 0, rangePct: 55);
           
            // 2. 解压：后台线程 + 批量 UI，总进度 55 → 82
            StatusText = "正在解压主程序...";
            string tempDir = Path.Combine(baseDir, "update_temp");
            if (Directory.Exists(tempDir))
                Directory.Delete(tempDir, true);
            await ExtractWithBatchedProgressAsync(zipPath, tempDir, basePct: 55, rangePct: 27);

            // 3. 复制：后台线程 + 批量 UI，总进度 82 → 100
            await CopyWithBatchedProgressAsync(tempDir, baseDir, basePct: 82, rangePct: 18);
            //调用
            await CleanTempFilesAsync(tempDir, zipPath);
           
        }
        private async Task CleanTempFilesAsync(string tempDir, string zipPath)
        {
            await Task.Run(() =>
            {
                try
                {
                    if (Directory.Exists(tempDir))
                        Directory.Delete(tempDir, true);

                    if (File.Exists(zipPath))
                        File.Delete(zipPath);
                }
                catch 
                {
                   
                }
            });
        }
        /// <summary>
        /// 更新插件：下载(0→55) → 解压(55→82) → 复制文件(82→100)
        /// </summary>
        public async Task UpdatePlugin(UpdateLinksConfig config)
        {
            var addinPath = InstallationPath();
            if (string.IsNullOrEmpty(addinPath)) return;

            XDocument doc = XDocument.Load(addinPath);
            var assemblyElement = doc.Descendants("Assembly").FirstOrDefault();
            if (assemblyElement == null) return;

            var assemblyPath = assemblyElement.Value;
            if (File.Exists(assemblyPath))
            {
                string? dir = Path.GetDirectoryName(Path.GetDirectoryName(assemblyPath));

                if (dir != null)
                {
                    Directory.Delete(dir, true);
                    Directory.CreateDirectory(dir);

                    // 1. 下载：总进度 0 → 55
                    string zipPath = Path.Combine(dir, "update.zip");
                    StatusText = "正在下载插件...";
                    Progressjd = 0;
                    ProgressNumerical = "0%";
                    await DownloadWithProgressAsync(config.ConstructionLink, zipPath, basePct: 0, rangePct: 55);

                    // 2. 解压：后台线程 + 批量 UI
                    StatusText = "正在解压插件...";
                    //string tempDir = Path.Combine(dir, "update_temp");
                    //if (Directory.Exists(tempDir))
                    //    Directory.Delete(tempDir, true);
                    await ExtractWithBatchedProgressAsync(zipPath, dir, basePct: 55, rangePct: 45);

                    // 如果解压出来多一层目录，取第一个子目录
                    //string[] topDirs = Directory.GetDirectories(tempDir);
                    //string sourceDir = topDirs.Length == 1 ? topDirs[0] : tempDir;

                    //// 3. 复制：后台线程 + 批量 UI
                    //await CopyWithBatchedProgressAsync(sourceDir, dir, basePct: 82, rangePct: 18);

                    //// 清理临时文件
                    //Directory.Delete(tempDir, true);
                    File.Delete(zipPath);
                }
            }
        }

        /// <summary>
        /// 读取本地更新地址配置；文件不存在时自动在 exe 同目录生成空模板
        /// </summary>
        private static UpdateLinksConfig LoadLinksConfig()
        {
            try
            {
                if (!File.Exists(_linksConfigPath))
                {
                    var defaultConfig = new UpdateLinksConfig();
                    var json = JsonSerializer.Serialize(defaultConfig, new JsonSerializerOptions { WriteIndented = true });
                    File.WriteAllText(_linksConfigPath, json);
                }

                var content = File.ReadAllText(_linksConfigPath);
                return JsonSerializer.Deserialize<UpdateLinksConfig>(content) ?? new UpdateLinksConfig();
            }
            catch (Exception)
            {
                // 解析失败返回空配置
            }
            return new UpdateLinksConfig();
        }
        /// <summary>
        /// 查找所有已安装的 Revit 版本对应的 .ADDIN 文件路径。
        /// </summary>
        /// <returns>所有找到的路径列表</returns>
        public static List<string> GetInstalledAddinPaths()
        {
            var result = new List<string>();
            var baseDir = "C:/ProgramData/Autodesk/Revit/Addins";
            if (!Directory.Exists(baseDir))
                return result;

            foreach (var dir in Directory.GetDirectories(baseDir))
            {
                var addinPath = Path.Combine(dir, "ApplicationSeries.ADDIN");
                if (File.Exists(addinPath))
                    result.Add(addinPath);
            }
            return result;
        }

        /// <summary>
        /// 查找第一个已安装的 Revit 插件 .ADDIN 文件路径（向后兼容）。
        /// </summary>
        private static string? InstallationPath()
        {
            return GetInstalledAddinPaths().FirstOrDefault();
        }
        public RelayCommand<Window?> CloseCommand => new((window) =>
        {
            if (window != null)
            {
                window.Close();
            }

        });
    }
    /// <summary>
    /// 本地更新地址配置文件模型
    /// </summary>
    public class UpdateLinksConfig
    {
        /// <summary>报建插件下载地址</summary>
        public string ConstructionLink { get; set; } = string.Empty;
        /// <summary>主程序下载地址</summary>
        public string AiConstructionLink { get; set; } = string.Empty;
        public string AiRemark { get; set; } = string.Empty;
        public string ConstructionRemark { get; set; } = string.Empty;
    }
    public class UpdateLogItem
    {
        public string Content { get; set; } = string.Empty;
    }
}
