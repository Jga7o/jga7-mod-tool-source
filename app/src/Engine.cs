// M17 session 4: the app's only door to the engine (app_engine.py, plan §3.1). The app does no modding itself.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace JgaModTool
{
    public static class Paths
    {
        public static string ToolDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');

        static string Env(string k) { string v = Environment.GetEnvironmentVariable(k); return string.IsNullOrEmpty(v) ? null : v; }

        // The bundled runtime (release) or, during development, JGA_PYTHON / JGA_SCRIPTS.
        public static string Python
        {
            get
            {
                string p = Path.Combine(ToolDir, @"engine\python\pythonw.exe");
                return File.Exists(p) ? p : (Env("JGA_PYTHON") ?? "pythonw.exe");
            }
        }
        public static string Scripts
        {
            get
            {
                string p = Path.Combine(ToolDir, @"engine\scripts");
                return Directory.Exists(p) ? p : (Env("JGA_SCRIPTS") ?? p);
            }
        }
        public static string EngineData { get { return Path.Combine(ToolDir, @"engine\data"); } }
        // JGA_HOME (tests): every folder of the tool under one root instead of %LOCALAPPDATA% / %USERPROFILE%.
        public static string Home { get { return Env("JGA_HOME") ?? Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData); } }
        public static string MakerData { get { return Path.Combine(Home, "AmiciaModMaker"); } }
        public static string ModsData { get { return Path.Combine(Home, "AmiciaMods"); } }
        public static string ModsOut
        {
            get
            {
                return Env("JGA_HOME") != null ? Path.Combine(Home, "Amicia Mods")
                    : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Amicia Mods");
            }
        }
        public static string KitsDir { get { return Path.Combine(MakerData, "kits"); } }
        public static string FakeSteam { get { return Env("JGA_STEAM"); } }   // tests: a fake Steam folder, never the registry
    }

    public class EngineResult
    {
        public int ExitCode;
        public Dictionary<string, object> Json;   // the last JSON line, or null
        public string Log = "";
        public bool Stopped;

        public bool Ok { get { return Json != null && Json.ContainsKey("ok") && Json["ok"] is bool && (bool)Json["ok"]; } }
        public Dictionary<string, object> Error
        {
            get { return Json != null && Json.ContainsKey("error") ? Json["error"] as Dictionary<string, object> : null; }
        }
        public string ErrorCode { get { var e = Error; return e != null && e.ContainsKey("code") ? (string)e["code"] : null; } }
        public string ErrorText
        {
            get
            {
                var e = Error;
                if (e != null && e.ContainsKey("text")) return (string)e["text"];
                if (Json == null) { string t = Log.Trim(); return "the engine stopped: " + (t.Length > 400 ? t.Substring(t.Length - 400) : t); }
                return null;
            }
        }
        public string Advice { get { var e = Error; return e != null && e.ContainsKey("advice") ? (string)e["advice"] : null; } }
    }

    public class EngineRun
    {
        Process proc;
        public volatile bool StopAsked;

        public static JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };

        // Runs app_engine.py with args; progress(i, n, text) for every PROGRESS line (on the worker thread).
        public EngineResult Run(string[] args, Action<int, int, string> progress)
        {
            var r = new EngineResult();
            var psi = new ProcessStartInfo(Paths.Python);
            var sb = new StringBuilder("-I \"" + Path.Combine(Paths.Scripts, "app_engine.py") + "\"");
            foreach (string a in args) sb.Append(" ").Append(Quote(a));
            psi.Arguments = sb.ToString();
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            // Release: engine\ mirrors output\'s layout (scripts\, analysis\, previews\, mod\, ...), so every script finds its
            // tables next to itself; only the hash list has its own place.
            if (File.Exists(Path.Combine(Paths.EngineData, "supported_hashes.txt")))
                psi.EnvironmentVariables["MODLAB_HASHES"] = Path.Combine(Paths.EngineData, "supported_hashes.txt");
            var log = new StringBuilder();
            string last = null;
            proc = Process.Start(psi);
            var err = new Thread(() => { string e = proc.StandardError.ReadToEnd(); lock (log) log.Append(e); });
            err.IsBackground = true;
            err.Start();
            string line;
            while ((line = proc.StandardOutput.ReadLine()) != null)
            {
                lock (log) log.AppendLine(line);
                if (line.StartsWith("PROGRESS "))
                {
                    string[] p = line.Substring(9).Split(new[] { ' ' }, 2);
                    string[] q = p[0].Split('/');
                    int i, n;
                    if (q.Length == 2 && int.TryParse(q[0], out i) && int.TryParse(q[1], out n) && progress != null)
                        progress(i, n, p.Length > 1 ? p[1] : "");
                }
                else if (line.StartsWith("{")) last = line;
            }
            proc.WaitForExit();
            err.Join(2000);
            r.ExitCode = proc.ExitCode;
            r.Log = log.ToString();
            r.Stopped = StopAsked;
            if (last != null)
            {
                try { r.Json = Json.Deserialize<Dictionary<string, object>>(last); } catch (Exception) { r.Json = null; }
            }
            return r;
        }

        // Stop: the engine and every process it started (build_mod.py runs as a child).
        public void Stop()
        {
            StopAsked = true;
            try
            {
                if (proc != null && !proc.HasExited)
                {
                    var k = new ProcessStartInfo("taskkill", "/T /F /PID " + proc.Id) { CreateNoWindow = true, UseShellExecute = false };
                    Process.Start(k).WaitForExit(10000);
                }
            }
            catch (Exception) { }
        }

        public static string Quote(string a)
        {
            if (a.Length > 0 && a.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return a;
            var sb = new StringBuilder("\"");
            int bs = 0;
            foreach (char c in a)
            {
                if (c == '\\') { bs++; continue; }
                if (c == '"') { sb.Append('\\', bs * 2 + 1); sb.Append('"'); }
                else { sb.Append('\\', bs); sb.Append(c); }
                bs = 0;
            }
            sb.Append('\\', bs * 2);
            return sb.Append('"').ToString();
        }
    }

    public static class J
    {
        public static string S(Dictionary<string, object> d, string k) { return d != null && d.ContainsKey(k) && d[k] != null ? d[k].ToString() : null; }
        public static Dictionary<string, object> D(Dictionary<string, object> d, string k) { return d != null && d.ContainsKey(k) ? d[k] as Dictionary<string, object> : null; }
        public static ArrayList A(Dictionary<string, object> d, string k) { return d != null && d.ContainsKey(k) ? d[k] as ArrayList : null; }
        public static bool B(Dictionary<string, object> d, string k) { return d != null && d.ContainsKey(k) && d[k] is bool && (bool)d[k]; }
        public static long N(Dictionary<string, object> d, string k)
        {
            if (d == null || !d.ContainsKey(k) || d[k] == null) return 0;
            try { return Convert.ToInt64(d[k]); } catch (Exception) { return 0; }
        }
    }
}
