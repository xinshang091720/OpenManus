using System.Configuration;
using System.Data;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Windows;

namespace AIContstructUpdate
{
    /// <summary>
    /// Interaction logic for App.xaml
    /// </summary>
    public partial class App : Application
    {
        static App()
        {
            AppDomain.CurrentDomain.AssemblyResolve += OnResolveAssembly;
        }

        private static Assembly? OnResolveAssembly(object? sender, ResolveEventArgs args)
        {
            var executingAssembly = Assembly.GetExecutingAssembly();
            var assemblyName = new AssemblyName(args.Name);
            var path = assemblyName.Name + ".dll";

            // 调试用：输出请求的名称和实际路径
            System.Diagnostics.Debug.WriteLine($"[AssemblyResolve] 请求: {args.Name}, 查找资源: {path}");

            if (assemblyName.CultureInfo != null && !assemblyName.CultureInfo.Equals(CultureInfo.InvariantCulture))
                path = $@"{assemblyName.CultureInfo}\{path}";

            var stream = executingAssembly.GetManifestResourceStream(path);
            if (stream == null)
            {
                // 列出所有可用资源名，方便排查
                var allResources = executingAssembly.GetManifestResourceNames();
                System.Diagnostics.Debug.WriteLine($"[AssemblyResolve] 未找到 {path}，可用资源: {string.Join(", ", allResources)}");
                return null;
            }

            using var ms = new MemoryStream();
            stream.CopyTo(ms);
            return Assembly.Load(ms.ToArray());
        }
    }

}
