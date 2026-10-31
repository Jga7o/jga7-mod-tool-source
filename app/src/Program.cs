// M17 session 4: Jga7 Mod Tool, the app (plan §1, §3; v5 §0.2). WinForms, .NET Framework 4.8, built with Windows' csc.exe.
//   JgaModTool.exe                 the app
//   JgaModTool.exe /apply <plan>   the elevated copy into the game folder (started by the app itself with "runas" when the
//                                  game folder is not writable): runs the engine's copy-in and writes <plan>.result.json
//   JgaModTool.exe /shots <dir>    tests: every page with example contents saved as PNG (no engine calls)
using System;
using System.Collections.Generic;
using System.IO;
using System.Windows.Forms;

namespace JgaModTool
{
    public static class Program
    {
        // Built into the exe on purpose (user, 2026-10-30): not read from config.json, so players cannot change them.
        // The author is always "Jga7", exactly this case. Links: the Gumroad and Nexus pages.
        // Stored masked (no plain text in the exe) with a check value: a copy whose name, author or links were changed
        // does not start (session 67, light protection; made by output\analysis\s67\make_ident.py, which holds the values).
        static readonly byte[] mToolName = { 13, 79, 114, 11, 49, 191, 182, 212, 185, 110, 60, 87, 122 };
        static readonly byte[] mAuthor = { 13, 79, 114, 11 };
        static readonly byte[] mGumroad = { 47, 92, 103, 76, 98, 200, 246, 159, 255, 94, 43, 75, 125, 146, 238, 241, 178, 79, 61, 79, 100, 140, 237, 203, 170, 15, 100, 75, 46, 157, 144, 146, 183, 246, 18 };
        static readonly byte[] mNexus = { 47, 92, 103, 76, 98, 200, 246, 159, 238, 77, 36, 22, 120, 147, 184, 227, 180, 79, 32, 68, 118, 198, 160, 199, 168, 77, 42, 87, 109, 144, 130, 131, 168, 234, 18, 56, 90, 123, 149, 250, 210, 249, 17, 62, 69, 81, 138, 241, 222, 230, 20, 97, 17, 50 };
        const uint IdentCheck = 0x79E7E0B2;
        static readonly byte[] Key = System.Text.Encoding.ASCII.GetBytes("G7-amicia-ember");
        static string Unmask(byte[] m)
        {
            var b = new byte[m.Length];
            for (int i = 0; i < m.Length; i++) b[i] = (byte)(m[i] ^ Key[i % Key.Length] ^ ((i * 31) & 0xFF));
            return System.Text.Encoding.UTF8.GetString(b);
        }
        public static readonly string ToolName = Unmask(mToolName), Author = Unmask(mAuthor), Gumroad = Unmask(mGumroad), Nexus = Unmask(mNexus);
        public static string Version = "1.0";
        static bool IdentOk()
        {
            uint h = 0x811C9DC5;
            foreach (byte x in System.Text.Encoding.UTF8.GetBytes(ToolName + "|" + Author + "|" + Gumroad + "|" + Nexus)) h = (h ^ x) * 0x01000193;
            return h == IdentCheck && Author == "Jga7" && ToolName.StartsWith("Jga7 ");
        }

        static void LoadConfig()
        {
            foreach (string p in new[] { Path.Combine(Paths.ToolDir, "config.json"), Environment.GetEnvironmentVariable("JGA_CONFIG") ?? "" })
            {
                if (p == "" || !File.Exists(p)) continue;
                try
                {
                    var d = EngineRun.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(p));
                    Version = J.S(d, "VERSION") ?? Version;
                }
                catch (Exception) { }
                return;
            }
        }

        [STAThread]
        static int Main(string[] args)
        {
            if (!IdentOk())
            {
                MessageBox.Show("This copy of the tool has been modified and will not start. Download the original from its page on Nexus Mods.", "Modified copy", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            LoadConfig();
            if (args.Length == 2 && args[0] == "/apply")
            {
                string plan = args[1];
                EngineResult r = new EngineRun().Run(new[] { "copy-in", plan }, null);
                string js = r.Json != null ? EngineRun.Json.Serialize(r.Json)
                    : EngineRun.Json.Serialize(new Dictionary<string, object> { { "ok", false }, { "error", new Dictionary<string, object> { { "code", "internal" }, { "text", r.ErrorText } } } });
                File.WriteAllText(plan + ".result.json", js);
                return r.Ok ? 0 : 1;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            // The engine must sit next to the exe (session 67: the user's copy had none, every step failed with "the
            // engine could not start: The system cannot find the file specified"). Development runs set JGA_PYTHON.
            string missing = !File.Exists(Path.Combine(Paths.ToolDir, @"engine\python\pythonw.exe")) && Environment.GetEnvironmentVariable("JGA_PYTHON") == null
                ? @"engine\python\pythonw.exe"
                : !File.Exists(Path.Combine(Paths.Scripts, "app_engine.py")) ? @"engine\scripts\app_engine.py" : null;
            if (missing != null)
            {
                bool fromZip = Paths.ToolDir.StartsWith(Path.GetTempPath().TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
                MessageBox.Show((fromZip
                    ? "The tool was started from inside its .zip, so its engine folder is not with it.\n\n"
                    : "The tool's engine is missing: " + missing + " is not next to JgaModTool.exe.\n\n")
                    + "Right-click the .zip > Extract All, then start JgaModTool.exe from the extracted \"" + Program.ToolName + "\" folder (keep the engine folder next to it).\n\n"
                    + "If you did that already, your antivirus may have removed the file: restore it, or allow the tool's folder.",
                    ToolName, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            var f = new MainForm();
            if (args.Length == 2 && args[0] == "/shots")
            {
                f.Shots(args[1]);
                return 0;
            }
            if (args.Length == 2 && args[0] == "/selftest") f.SelfTest(args[1]);
            Application.Run(f);
            return 0;
        }
    }
}
