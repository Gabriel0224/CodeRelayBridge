// Open the stock Codex side chat only in the active document named by the client.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Automation;
using System.Windows.Forms;
class OpenCodexSideChat {
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [STAThread] static int Main(string[] args) {
        try {
            int pid;
            if (args.Length != 1 || !int.TryParse(args[0], out pid)) return 2;
            Console.InputEncoding = Encoding.UTF8;
            var title = Console.In.ReadLine();
            if (String.IsNullOrWhiteSpace(title) || title.Length > 512) return 2;
            var app = Process.GetProcessById(pid);
            var expected = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                @"WindowsApps\OpenAI.Codex_26.1120.6357.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe");
            var window = app.MainWindowHandle;
            if (window == IntPtr.Zero || app.SessionId != Process.GetCurrentProcess().SessionId ||
                !String.Equals(expected, app.MainModule.FileName, StringComparison.OrdinalIgnoreCase) ||
                GetForegroundWindow() != window) return 3;
            var docs = AutomationElement.FromHandle(window).FindAll(TreeScope.Descendants,
                new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Document));
            int count = 0;
            foreach (AutomationElement doc in docs) {
                if (doc.Current.IsOffscreen) continue;
                if (doc.Current.Name != title || !doc.Current.IsEnabled) return 4;
                count++;
            }
            if (count != 1 || GetForegroundWindow() != window) return 4;
            // Only this app shortcut is accepted. No user-supplied keys are interpreted.
            SendKeys.SendWait("^%s");
            return 0;
        } catch (Exception) { return 5; }
    }
}
