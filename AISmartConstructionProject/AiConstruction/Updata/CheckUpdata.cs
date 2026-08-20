using AiConstruction.Api;
using AiConstruction.Model;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net.Http;
using System.Reflection;
using System.Text.Json;
using System.Xml.Linq;

namespace AiConstruction.Updata
{
    public static class CheckUpdata
    {
        private static List<VersionInformation>? _cachedVersions;
        private static DateTime _cacheTime = DateTime.MinValue;
        private static readonly TimeSpan _cacheExpiry = TimeSpan.FromMinutes(5);

        // 本地更新地址配置文件路径
        private static readonly string _linksConfigPath = Path.Combine(
            AppDomain.CurrentDomain.BaseDirectory, "update_links.json");

        /// <summary>
        /// 获取线上版本信息（带 5 分钟缓存，避免重复请求）
        /// </summary>
        public static List<VersionInformation>? GetVersion(int types,bool forceRefresh = false)
        {
            //if (!forceRefresh && _cachedVersions != null && DateTime.Now - _cacheTime < _cacheExpiry)
            //    return _cachedVersions;

            try
            {
                var apiClient = new AccountApiClient();
                var versions = apiClient.GetVersionInformation(types);
                _cachedVersions = versions?.Data;
                _cacheTime = DateTime.Now;
                return _cachedVersions;
            }
            catch (Exception)
            {
                return _cachedVersions; // 请求失败时返回上次缓存（如有）
            }
        }

        /// <summary>
        /// 检查 Revit 插件（工规报建）是否有更新。true=有更新，false=无更新或异常
        /// </summary>
        public static bool ConstructionUpdate(string name)
        {
            try
            {
                var versions = GetVersion(1);
                if (versions == null || versions.Count == 0)
                {
                    ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.ConstructionLink, UpdateLinkField.ConstructionRemark });
                    return false;
                }

                var onlineVersion = versions.FirstOrDefault(x => x.VersionName == name);
                if (onlineVersion == null || string.IsNullOrWhiteSpace(onlineVersion.ReleaseNumber))
                {
                    ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.ConstructionLink, UpdateLinkField.ConstructionRemark });
                    return false;
                }

                var addinPath = InstallationPath();
                if (string.IsNullOrEmpty(addinPath))
                {
                    ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.ConstructionLink, UpdateLinkField.ConstructionRemark });
                    return false;
                }

                var doc = XDocument.Load(addinPath);
                var assemblyElement = doc.Descendants("Assembly").FirstOrDefault();
                if (assemblyElement == null || string.IsNullOrWhiteSpace(assemblyElement.Value))
                {
                    ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.ConstructionLink, UpdateLinkField.ConstructionRemark });
                    return false;
                }

                var assemblyPath = assemblyElement.Value;
                if (!File.Exists(assemblyPath))
                {
                    ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.ConstructionLink, UpdateLinkField.ConstructionRemark });
                    return false;
                }

                var fileVersionInfo = FileVersionInfo.GetVersionInfo(assemblyPath);
                var localVersion = fileVersionInfo.FileVersion;
                if (string.IsNullOrWhiteSpace(localVersion))
                {
                    ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.ConstructionLink, UpdateLinkField.ConstructionRemark });
                    return false;
                }

                bool hasUpdate = IsOnlineNewer(onlineVersion.ReleaseNumber, localVersion);
                if (hasUpdate)
                    SaveUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.ConstructionLink, UpdateLinkField.ConstructionRemark }, onlineVersion.ApplicationLinks, onlineVersion.Remark);
                else
                    ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.ConstructionLink, UpdateLinkField.ConstructionRemark });
                return hasUpdate;
            }
            catch (Exception)
            {
                ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.ConstructionLink, UpdateLinkField.ConstructionRemark });
                return false;
            }
        }

        //获取AI智建 更新程序包
        public static VersionInformation GetAiUpdatePage()
        {
            var versions = GetVersion(2);
            if (versions == null || versions.Count == 0)
            {
                return new VersionInformation();
            }
            return versions.FirstOrDefault(x=>x.VersionName=="AI智建更新包")??new VersionInformation();
        }

        /// <summary>
        /// 检查主程序（AiConstruction）是否有更新。true=有更新，false=无更新或异常
        /// </summary>
        public static bool AiConstructionUpdate(string name)
        {
            try
            {
                var versions = GetVersion(2);
                if (versions == null || versions.Count == 0)
                {
                    ClearUpdateLink( new List<UpdateLinkField>() { UpdateLinkField.AiConstructionLink, UpdateLinkField.AiRemark } );
                    return false;
                }

                var onlineVersion = versions.FirstOrDefault(x => x.VersionName == name);
                if (onlineVersion == null || string.IsNullOrWhiteSpace(onlineVersion.ReleaseNumber))
                {
                    ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.AiConstructionLink, UpdateLinkField.AiRemark });
                    return false;
                }

                var asm = Assembly.GetExecutingAssembly();
                var fileVersionInfo = FileVersionInfo.GetVersionInfo(asm.Location);
                var localVersion = fileVersionInfo.FileVersion;
                if (string.IsNullOrWhiteSpace(localVersion))
                {
                    ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.AiConstructionLink, UpdateLinkField.AiRemark });
                    return false;
                }

                bool hasUpdate = IsOnlineNewer(onlineVersion.ReleaseNumber, localVersion);
                if (hasUpdate)
                    SaveUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.AiConstructionLink, UpdateLinkField.AiRemark }, onlineVersion.ApplicationLinks,onlineVersion.Remark);
                else
                    ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.AiConstructionLink, UpdateLinkField.AiRemark });
                return hasUpdate;
            }
            catch (Exception)
            {
                ClearUpdateLink(new List<UpdateLinkField>() { UpdateLinkField.AiConstructionLink, UpdateLinkField.AiRemark });
                return false;
            }
        }

        /// <summary>
        /// 比较线上版本是否比本地版本更新。
        /// 支持带或不带 'V' 前缀，支持 2~4 段版本号（如 1.2 / 1.2.3 / 1.2.3.4）。
        /// </summary>
        /// <param name="onlineRaw">线上版本原始字符串（如 "V1.2.3"）</param>
        /// <param name="localRaw">本地版本字符串（如 "1.2.3"）</param>
        /// <returns>true=线上更新，false=相同或更旧</returns>
        public static bool IsOnlineNewer(string onlineRaw, string? localRaw)
        {
            if (string.IsNullOrWhiteSpace(onlineRaw) || string.IsNullOrWhiteSpace(localRaw))
                return false;

            // 去掉 V 前缀
            var online = onlineRaw.TrimStart('V', 'v').Trim();
            var local = localRaw.TrimStart('V', 'v').Trim();

            // 优先用 .NET 内置 Version 比较（支持 2~4 段）
            if (Version.TryParse(online, out var onlineVer) && Version.TryParse(local, out var localVer))
                return onlineVer > localVer;

            // fallback：逐段比较
            var oParts = online.Split('.');
            var lParts = local.Split('.');
            int maxLen = oParts.Length >= lParts.Length ? oParts.Length : lParts.Length;
            for (int i = 0; i < maxLen; i++)
            {
                int o = i < oParts.Length && int.TryParse(oParts[i], out var ov) ? ov : 0;
                int l = i < lParts.Length && int.TryParse(lParts[i], out var lv) ? lv : 0;
                if (o > l) return true;
                if (o < l) return false;
                // 相等则继续比较下一段
            }
            return false; // 完全相等
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

        // ==================== 本地更新地址配置 ====================

        /// <summary>
        /// 更新地址字段标识
        /// </summary>
        public enum UpdateLinkField
        {
            /// <summary>报建插件下载地址</summary>
            ConstructionLink,
            /// <summary>主程序下载地址</summary>
            AiConstructionLink,

            AiRemark,
            ConstructionRemark,

        }

        /// <summary>
        /// 有更新时保存下载地址到本地配置文件（文件不存在则创建）
        /// </summary>
        private static void SaveUpdateLink(List<UpdateLinkField>  fields, string link,string remark)
        {
            try
            {
                var config = LoadLinksConfig();
                foreach(var field in fields)
                {
                    switch (field)
                    {
                        case UpdateLinkField.ConstructionLink:
                            config.ConstructionLink = link ?? string.Empty;
                            break;
                        case UpdateLinkField.AiConstructionLink:
                            config.AiConstructionLink = link ?? string.Empty;
                            break;
                        case UpdateLinkField.AiRemark:
                            config.AiRemark = remark ?? string.Empty;
                            break;
                        case UpdateLinkField.ConstructionRemark:
                            config.ConstructionRemark = remark ?? string.Empty;
                            break;
                    }
                }
           
                SaveLinksConfig(config);
            }
            catch (Exception)
            {
                // 写入失败不影响主流程
            }
        }

        /// <summary>
        /// 无更新时清空对应字段（文件不存在则忽略）
        /// </summary>
        private static void ClearUpdateLink(List< UpdateLinkField> fields)
        {
            try
            {
                if (!File.Exists(_linksConfigPath))
                    return; // 文件不存在，不用管

                var config = LoadLinksConfig();
                foreach (var field in fields)
                {
                    switch (field)
                    {
                        case UpdateLinkField.ConstructionLink:
                            config.ConstructionLink = string.Empty;
                            break;
                        case UpdateLinkField.AiConstructionLink:
                            config.AiConstructionLink = string.Empty;
                            break;
                        case UpdateLinkField.AiRemark:
                            config.AiRemark = string.Empty;
                            break;
                        case UpdateLinkField.ConstructionRemark:
                            config.ConstructionRemark = string.Empty;
                            break;
                    }
                }

                SaveLinksConfig(config);
            }
            catch (Exception)
            {
                // 清空失败不影响主流程
            }
        }

        /// <summary>
        /// 读取本地更新地址配置
        /// </summary>
        private static UpdateLinksConfig LoadLinksConfig()
        {
            try
            {
                if (File.Exists(_linksConfigPath))
                {
                    var json = File.ReadAllText(_linksConfigPath);
                    return JsonSerializer.Deserialize<UpdateLinksConfig>(json) ?? new UpdateLinksConfig();
                }
            }
            catch (Exception)
            {
                // 解析失败返回空配置
            }
            return new UpdateLinksConfig();
        }

        /// <summary>
        /// 写入本地更新地址配置
        /// </summary>
        private static void SaveLinksConfig(UpdateLinksConfig config)
        {
            var json = JsonSerializer.Serialize(config, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(_linksConfigPath, json);
        }
        /// <summary>
        /// 异步下载文件，无进度回调
        /// </summary>
        /// <param name="downloadUrl">下载地址</param>
        /// <param name="saveFilePath">本地保存完整路径</param>
        public static async Task DownloadZipAsync(string downloadUrl, string saveFilePath)
        {
            // 创建保存目录
            string? saveDir = Path.GetDirectoryName(saveFilePath);
            if (!string.IsNullOrEmpty(saveDir) && !Directory.Exists(saveDir))
            {
                Directory.CreateDirectory(saveDir);
            }
            using var client = new HttpClient { Timeout = TimeSpan.FromMinutes(10) };
            using var response = await client.GetAsync(downloadUrl, HttpCompletionOption.ResponseHeadersRead);
            response.EnsureSuccessStatusCode();

            using var readStream = await response.Content.ReadAsStreamAsync();
            using var writeStream = new FileStream(
                saveFilePath,
                FileMode.Create,
                FileAccess.Write,
                FileShare.None,
                bufferSize: 8192,
                useAsync: true);

            await readStream.CopyToAsync(writeStream);
            await writeStream.FlushAsync();
        }
        public static async Task ExtractWithBatchedProgressAsync(string zipPath, string destDir)
        {
            await Task.Run(() =>
            {
                using var archive = ZipFile.OpenRead(zipPath);
                foreach (var entry in archive.Entries)
                {
                    string destPath = Path.Combine(destDir, entry.FullName);
                    if (entry.FullName.EndsWith('/') || entry.FullName.EndsWith('\\'))
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
                }
            });
        }

        /// <summary>
        /// 后台线程复制文件，跳过正在运行的更新exe，无UI进度更新
        /// </summary>
        public static async Task CopyWithBatchedProgressAsync(string sourceDir, string targetDir)
        {
            await Task.Run(() =>
            {
                var allFiles = Directory.GetFiles(sourceDir, "*", SearchOption.AllDirectories);

                foreach (string file in allFiles)
                {
                    string fileName = Path.GetFileName(file);
                    string relativePath = file.Substring(sourceDir.Length).TrimStart('\\', '/');
                    string targetPath = Path.Combine(targetDir, relativePath);
                    string? parentDir = Path.GetDirectoryName(targetPath);

                    if (!string.IsNullOrEmpty(parentDir) && !Directory.Exists(parentDir))
                    {
                        Directory.CreateDirectory(parentDir);
                    }

                    File.Copy(file, targetPath, true);
                }
            });
        }
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
 
     
}
