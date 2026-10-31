// M17 session 4: the pages (plan §1, v5 §0.2; mockups output\app_mockups v5). Wording follows the mockups.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Threading;
using System.Windows.Forms;

namespace JgaModTool
{
    public class Part
    {
        public string File;
        public Dictionary<string, object> Info;                        // engine "inspect"
        public Dictionary<string, string> Images = new Dictionary<string, string>();   // colour / normal / roughness / metallic chosen on the page
        public string Overlay;                                         // Advanced
        public int TexSize
        {
            get
            {
                string c;
                if (Images.TryGetValue("colour", out c)) { var s = U.PngSize(c); if (s.Width > 0) return s.Width; }
                var t = J.D(J.D(Info, "textures"), "colour");
                var sz = J.A(t, "size");
                return sz != null ? Convert.ToInt32(sz[0]) : 0;
            }
        }
    }

    public partial class MainForm : Form
    {
        // ---------- what the pages chose ----------
        bool wantModel = true, wantHair, wantEyes;
        string game, gameText, gameKind = "wait";      // gameKind: wait / ok / bad / none
        bool prepared;
        Part cloth = new Part(), skin = new Part();
        string hairFile, hairColour, hairAlpha, hairRgba, hairNormal, eyeColour;
        Dictionary<string, object> hairInfo;
        public HashSet<string> Groups = new HashSet<string> { "chp1", "chp2", "main", "final" };
        public bool KeepWork;
        ArrayList checkFatal, checkNotes;
        string checkState = "";                        // "" / running / done / failed
        string checkFor = "";                          // "model" / "hair": whose check the three fields hold
        bool checkBusy;                                // an engine check is running
        Action checkNext;                              // the newest check asked for meanwhile
        string lastModFolder, lastModName;
        string buildState = "ready";                   // ready / building / done / failed
        string buildText = "", buildLog = "", buildResultText = "";
        EngineRun running;

        // install page
        string modPath;
        Dictionary<string, object> modJson;
        Image modPreview;
        string installGameKind = "wait", installGameText = "", installStateText = "", installStateKind = "ok", installMsg, installMsgKind;
        Dictionary<string, object> installState;

        // ---------- layout ----------
        Panel steps, scroll, foot;
        FlowLayoutPanel footL, footR;
        Column body;
        string page = "home";
        const int W = 1040, Inner = 940;
        bool demo, selfTesting;

        public MainForm()
        {
            Text = Program.ToolName;
            ClientSize = new Size(W, 760);
            MinimumSize = new Size(820, 560);
            Theme.Dark(this);
            Font = Theme.Body;
            KeyPreview = true;   // Ctrl+V: a copied file goes into the drop box under the mouse, else the first empty one
            KeyDown += (s, e) =>
            {
                if (!(e.Control && e.KeyCode == Keys.V) || ActiveControl is TextBox) return;
                var target = DropBox.Hovered != null && !DropBox.Hovered.IsDisposed ? DropBox.Hovered : null;
                string clip = DropBox.ClipboardPath();
                if (target == null && clip != null)
                    target = AllBoxes(scroll).FirstOrDefault(b => b.File == null && b.Takes(clip));
                if (target != null) { target.Paste(); e.Handled = e.SuppressKeyPress = true; }
            };
            StartPosition = FormStartPosition.CenterScreen;
            foot = new Panel { Dock = DockStyle.Bottom, Height = 96, BackColor = Theme.Foot, Padding = new Padding(24, 12, 24, 8) };
            foot.Paint += (s, e) => { using (var p = new Pen(Theme.Line)) e.Graphics.DrawLine(p, 0, 0, foot.Width, 0); };
            footL = new FlowLayoutPanel { Dock = DockStyle.Fill, WrapContents = true, BackColor = Color.Transparent };
            footR = new FlowLayoutPanel { Dock = DockStyle.Right, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, FlowDirection = FlowDirection.RightToLeft, BackColor = Color.Transparent };
            foot.Controls.Add(footL); foot.Controls.Add(footR);
            steps = new Panel { Dock = DockStyle.Top, Height = 46, BackColor = Theme.Foot };
            steps.Paint += (s, e) => { using (var p = new Pen(Theme.Line)) e.Graphics.DrawLine(p, 0, steps.Height - 1, steps.Width, steps.Height - 1); };
            scroll = new Panel { Dock = DockStyle.Fill, AutoScroll = true, BackColor = Theme.Win, Padding = new Padding(28, 18, 28, 18) };
            Controls.Add(scroll); Controls.Add(steps); Controls.Add(foot);
            Resize += (s, e) => { if (body != null) Show(page); };
            // at start: find the game and read its state (Home's "Last installed mod", the game-update check)
            Shown += (s, e) => { if (!demo && !selfTesting) { startCheck = true; DetectGame(true); } };
            Show("home");
        }

        // ---------- the step bar ----------
        List<string> Flow()
        {
            var f = new List<string> { "What to change" };
            if (wantModel) f.Add("Model");
            if (wantHair) f.Add("Hair");
            if (wantEyes) f.Add("Eyes");
            f.Add("Build");
            return f;
        }
        static readonly Dictionary<string, string> StepOf = new Dictionary<string, string> { { "what", "What to change" }, { "model", "Model" }, { "hair", "Hair" }, { "eyes", "Eyes" }, { "build", "Build" } };
        static readonly Dictionary<string, string> PageOf = new Dictionary<string, string> { { "What to change", "what" }, { "Model", "model" }, { "Hair", "hair" }, { "Eyes", "eyes" }, { "Build", "build" } };

        void DrawSteps()
        {
            steps.Controls.Clear();
            string cur;
            steps.Visible = StepOf.TryGetValue(page, out cur);
            if (!steps.Visible) return;
            var f = Flow();
            int k = f.IndexOf(cur), x = 22;
            for (int i = 0; i < f.Count; i++)
            {
                var l = new Label { Text = f[i], AutoSize = true, Font = Theme.Small, Padding = new Padding(10, 4, 10, 4), Location = new Point(x, 10),
                                    BackColor = i == k ? Theme.Acc : i < k ? Theme.Acc2 : Theme.Pill, ForeColor = i == k ? Color.White : i < k ? Theme.AccInk : Theme.Mute };
                steps.Controls.Add(l);
                x += l.PreferredWidth + 8;
            }
        }

        string NextPage(string from)
        {
            var f = Flow();
            int i = f.IndexOf(StepOf[from]);
            return PageOf[f[Math.Min(i + 1, f.Count - 1)]];
        }
        string PrevPage(string from)
        {
            var f = Flow();
            int i = f.IndexOf(StepOf[from]);
            return i <= 0 ? "home" : PageOf[f[i - 1]];
        }

        // ---------- page switch ----------
        // Every change rebuilds the page. Drawing is frozen meanwhile (WM_SETREDRAW) and the window is drawn once at
        // the end, so it does not flash or jump (session 67: "the tool twitches on every click"). The old controls
        // are freed after the click that caused the change has finished.
        [System.Runtime.InteropServices.DllImport("user32.dll")]
        static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
        int frozen;
        void Freeze() { if (frozen++ == 0 && IsHandleCreated) SendMessage(Handle, 0x000B, IntPtr.Zero, IntPtr.Zero); }
        void Thaw()
        {
            if (--frozen > 0 || !IsHandleCreated) return;
            SendMessage(Handle, 0x000B, (IntPtr)1, IntPtr.Zero);
            Invalidate(true);
        }
        static void FreeLater(Control owner, List<Control> old)
        {
            if (old.Count == 0) return;
            owner.BeginInvoke((Action)(() => { foreach (var c in old) c.Dispose(); }));
        }

        static IEnumerable<DropBox> AllBoxes(Control c)
        {
            foreach (Control x in c.Controls)
            {
                var d = x as DropBox;
                if (d != null) yield return d;
                foreach (var y in AllBoxes(x)) yield return y;
            }
        }

        public void Show(string p)
        {
            Freeze();
            try { ShowPage(p); }
            finally { Thaw(); }
        }

        void ShowPage(string p)
        {
            page = p;
            SuspendLayout();
            var old = scroll.Controls.Cast<Control>().Concat(footL.Controls.Cast<Control>()).Concat(footR.Controls.Cast<Control>()).ToList();
            scroll.Controls.Clear();
            footL.Controls.Clear(); footR.Controls.Clear();
            if (IsHandleCreated) FreeLater(this, old);
            U.NewTips(this);   // a fresh ToolTip per page (the shared one stopped showing after a rebuild)
            int inner = Math.Max(600, scroll.ClientSize.Width - 60);
            body = new Column { Inner = inner, Location = new Point(28, 18) };
            switch (p)
            {
                case "home": PageHome(inner); break;
                case "what": PageWhat(inner); break;
                case "model": PageModel(inner); break;
                case "hair": PageHair(inner); break;
                case "eyes": PageEyes(inner); break;
                case "build": PageBuild(inner); break;
                case "install": PageInstall(inner); break;
            }
            scroll.Controls.Add(body);
            DrawSteps();
            ResumeLayout(true);
            // the footer grows to fit its buttons and their hints (they wrap onto a second row on narrow windows)
            int avail = Math.Max(200, foot.ClientSize.Width - foot.Padding.Horizontal - footR.PreferredSize.Width - 8);
            int need = Math.Max(footL.GetPreferredSize(new Size(avail, 0)).Height, footR.PreferredSize.Height) + foot.Padding.Vertical + 4;
            if (foot.Height != need) foot.Height = need;
        }
        void Refresh(string p)
        {
            if (page != p) return;
            Freeze();
            try { int y = scroll.VerticalScroll.Value; ShowPage(p); scroll.AutoScrollPosition = new Point(0, y); }
            finally { Thaw(); }
        }

        void FootL(params Control[] cs) { foreach (var c in cs) footL.Controls.Add(c); }
        void FootR(params Control[] cs) { foreach (var c in cs) footR.Controls.Add(c); }
        Control AdvBtn() { return U.BtnHint("Advanced…", "Game file details and extra options", false, (s, e) => new AdvancedForm(this).ShowDialog(this)); }
        Control ExportBtn() { return U.BtnHint("How to export your model", "The exact export settings, step by step", false, (s, e) => Dialogs.ExportHelp(this)); }

        // Runs the engine off the UI thread; done(result) on the UI thread.
        void Bg(string[] args, Action<int, int, string> progress, Action<EngineResult> done)
        {
            var run = new EngineRun();
            running = run;
            var t = new Thread(() =>
            {
                EngineResult r;
                try { r = run.Run(args, progress == null ? null : (Action<int, int, string>)((i, n, s) => BeginInvoke(progress, i, n, s))); }
                catch (Exception e) { r = new EngineResult { Log = "the engine could not start: " + e.Message }; }
                try { BeginInvoke(done, r); } catch (InvalidOperationException) { }
            });
            t.IsBackground = true;
            t.Start();
        }

        // ---------- 01 Home ----------
        void PageHome(int inner)
        {
            body.Add(U.H1(Program.ToolName));
            body.Add(U.Text("", Theme.Small, Theme.Mute, 0, 6));
            var row = U.Row(BigChoice("Make a mod", "Turn your model, hair or eye color into a mod.", inner, () => { Show("what"); DetectGame(false); }),
                            BigChoice("Install a mod", "Put a mod into your game, or put the original files back.", inner, () => { Show("install"); DetectGame(true); }));
            body.Add(row);
            if (installStateText != "") body.Add(U.Check(installStateKind, installStateText, inner - 18));
            FootL(U.BtnHint("Open mods folder", "Your built mods and their .zip files: " + Paths.ModsOut, false, (s, e) => { Directory.CreateDirectory(Paths.ModsOut); U.Open(Paths.ModsOut); }));
            FootR(U.BtnHint("About", "Who made the tool, and its pages", false, (s, e) => Dialogs.About(this)));
        }
        Control BigChoice(string title, string hint, int inner, Action go)
        {
            int w = (inner - 24) / 2;
            var p = new Panel { Width = w, Height = 150, Margin = new Padding(0, 10, 18, 10), Cursor = Cursors.Hand, BackColor = Theme.Card, Padding = new Padding(3) };
            var t = new Label { Text = title, Font = Theme.Big, AutoSize = false, Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, ForeColor = Theme.Ink };
            p.Controls.Add(t);
            U.Tip(p, hint); U.Tip(t, hint);   // the explanation shows on hover only (user, 2026-10-31)
            bool hot = false;
            p.Paint += (s, e) => { using (var pen = new Pen(hot ? Theme.AccInk : Theme.Line, 2)) e.Graphics.DrawRectangle(pen, 1, 1, p.Width - 3, p.Height - 3); };
            foreach (Control c in new Control[] { p, t })
            {
                c.Click += (s, e) => go();
                c.MouseEnter += (s, e) => { hot = true; p.BackColor = Theme.Acc2; p.Invalidate(); };
                c.MouseLeave += (s, e) => { hot = false; p.BackColor = Theme.Card; p.Invalidate(); };
            }
            return p;
        }

        // ---------- the game folder ----------
        void DetectGame(bool forInstall)
        {
            if (demo) return;
            if (game != null) { CheckGame(game, forInstall); return; }
            var args = new List<string> { "detect-game" };
            if (Paths.FakeSteam != null) { args.Add("--steam"); args.Add(Paths.FakeSteam); }
            Bg(args.ToArray(), null, r =>
            {
                string g = J.S(r.Json, "game");
                if (g == null)
                {
                    gameKind = installGameKind = "none";
                    gameText = installGameText = "Game not found.";
                    Refresh("what"); Refresh("install");
                    return;
                }
                CheckGame(g, forInstall);
            });
        }

        void CheckGame(string g, bool forInstall)
        {
            game = g;
            Bg(new[] { "state", g, "--data", Paths.ModsData }, null, r =>
            {
                if (r.Json == null)
                {
                    gameKind = installGameKind = "bad"; gameText = installGameText = "✕ The game folder cannot be read: " + r.ErrorText;
                    installState = null; installStateText = "";   // no stale "Original game files" under the error
                }
                else
                {
                    installState = r.Json;
                    var files = J.D(r.Json, "files");
                    bool allOriginal = files != null && files.Values.All(v => (string)v == "original");
                    bool supported = J.B(r.Json, "supported");
                    var inst = J.D(r.Json, "installed");
                    // Make a mod reads the originals: they must all be the Steam originals (a mod installed: restore first).
                    if (allOriginal) { gameKind = "ok"; gameText = "✓ Game found: " + g + " · Steam version"; }
                    else if (supported && inst != null) { gameKind = "bad"; gameText = "✕ A mod is installed (" + J.S(inst, "name") + "). Restore the original files first (Install a mod › Restore)."; }
                    else { gameKind = "bad"; gameText = "✕ Unsupported version of the game: only the Steam version is supported."; }
                    if (J.B(r.Json, "steam_updated"))
                    { installGameKind = "warn"; installGameText = "! Steam updated the game: the mod is no longer installed. Nothing was changed."; }
                    else if (!supported)
                    { installGameKind = "bad"; installGameText = "✕ Your game files are not the original ones and not a mod from this tool. Let Steam check them (Library › the game › Properties › Installed Files › Verify integrity), then try again."; }
                    else { installGameKind = "ok"; installGameText = "✓ Game: " + g + " · Steam version"; }
                    // Last installed mod and game-update check (user, 2026-10-31): shown on Home and Install; a pop-up
                    // once at start when Steam changed the game under an installed mod.
                    string modName = inst != null ? (J.S(inst, "name") + " " + J.S(inst, "version")).Trim() : null;
                    string when = J.S(r.Json, "date") != null && J.S(r.Json, "date").Length >= 10 ? " · " + J.S(r.Json, "date").Substring(0, 10) : "";
                    string popTitle = null, popText = null, popFix = null;
                    if (J.B(r.Json, "interrupted")) { installStateKind = "warn"; installStateText = "! The last install did not finish. Press Restore to put your original files back."; }
                    else if (J.B(r.Json, "steam_updated"))
                    {
                        installStateKind = "warn"; installStateText = "! Steam updated the game: " + modName + " is no longer fully installed. Press Restore to put back the files Steam did not update.";
                        popTitle = "! The game was updated"; popText = "Steam updated the game, so your mod (" + modName + ") is no longer fully installed.";
                        popFix = "Open Install a mod and press Restore: it puts back only the files Steam did not update and clears the old backups. The files Steam updated are never written over.";
                    }
                    else if (J.B(r.Json, "steam_verified"))
                    {
                        installStateKind = "warn"; installStateText = "! " + modName + " is no longer in the game: Steam put the original files back. Install it again to use it.";
                        popTitle = "! Your mod is no longer installed"; popText = "Steam put the original game files back (an update or Verify integrity), so " + modName + " is no longer in the game.";
                        popFix = "Install it again (Install a mod) if you want to keep using it. Your game is fine as it is.";
                    }
                    else if (inst != null) { installStateKind = "ok"; installStateText = "✓ Last installed mod: " + modName + when + " (in the game now)"; }
                    else if (allOriginal) { installStateKind = "ok"; installStateText = "✓ No mod installed: original game files"; }
                    else installStateText = "";
                    if (startCheck && popTitle != null && !demo && !selfTesting)
                        BeginInvoke((Action)(() => Dialogs.Problems(this, popTitle, Item(popText, popFix), false, null)));
                }
                startCheck = false;
                Refresh("home"); Refresh("what"); Refresh("install");
            });
        }
        bool startCheck;

        void ChangeGame(bool forInstall)
        {
            using (var d = new FolderBrowserDialog { Description = "Choose the game folder (the one with the DATAS folder inside)" })
            {
                if (d.ShowDialog(this) != DialogResult.OK) return;
                // the DATAS folder itself is fine too (session 67: the user picked DATAS and was refused)
                string g = d.SelectedPath.TrimEnd('\\');
                if (!File.Exists(Path.Combine(g, @"DATAS\P_AMICIA.DPC")) && File.Exists(Path.Combine(g, "P_AMICIA.DPC")))
                    g = Path.GetDirectoryName(g);
                if (g == null || !File.Exists(Path.Combine(g, @"DATAS\P_AMICIA.DPC")))
                {
                    MessageBox.Show(this, "This is not the game folder: choose \"A Plague Tale Innocence\" (the folder with DATAS inside) or DATAS itself.", Program.ToolName);
                    return;
                }
                prepared = false;
                gameKind = installGameKind = "wait";
                CheckGame(g, forInstall);
                Refresh("what"); Refresh("install");
            }
        }

        Control GameLine(string kind, string text, bool forInstall)
        {
            if (kind == "wait") return U.Check("", "Looking for the game…", body.Inner);
            if (kind == "none")
                return U.Row(U.Check("bad", "Game not found.", body.Inner - 120), U.Small("Browse…", (s, e) => ChangeGame(forInstall)));
            return U.Row(U.Check(kind, text, body.Inner - 120), U.Small("Change…", (s, e) => ChangeGame(forInstall)));
        }

        // ---------- 02 What to change ----------
        void PageWhat(int inner)
        {
            body.Add(U.H1("What do you want to change?"));
            body.Add(U.Sub("Tick one or more. You get one page for each."));
            var m = U.Tick("Model", "Amicia's body: clothes and skin, from your model", wantModel, inner);
            var h = U.Tick("Hair", "a new hairstyle, from your model and its images", wantHair, inner);
            var e = U.Tick("Eyes", "the eye color, from one image", wantEyes, inner);
            m.CheckedChanged += (s, a) => { wantModel = m.Checked; Show("what"); };
            h.CheckedChanged += (s, a) => { wantHair = h.Checked; Show("what"); };
            e.CheckedChanged += (s, a) => { wantEyes = e.Checked; Show("what"); };
            body.Add(m); body.Add(h); body.Add(e);
            body.Add(U.Banner("This tool replaces Amicia's model in all chapters.   (To leave some chapters unchanged: Advanced.)", inner));
            body.Add(U.H2("Game"));
            body.Add(GameLine(gameKind, gameText, false));
            body.Add(U.Hint("The mod is made from your own game files. \"Change…\" picks another game folder."));
            FootL(U.BtnHint("Back", "Return to the first page", false, (s, a) => Show("home")));
            Button next;
            FootR(U.BtnHint("Next", "Go to the first ticked part", true, (s, a) => Prepare(() => Show(NextPage("what"))), out next));
            next.Enabled = (wantModel || wantHair || wantEyes) && gameKind == "ok" && Groups.Count > 0;
            FootL(AdvBtn());
        }

        // The first time: verified copies of the originals, unpacked (engine "prepare"); later only checked.
        void Prepare(Action then)
        {
            if (prepared || demo) { then(); return; }
            var dlg = new ProgressDialog("Reading your game files", "The first time takes a few minutes: the tool copies and reads your original game files. Your game is not changed.");
            Bg(new[] { "prepare", game, "--data", Paths.MakerData }, (i, n, t) => dlg.Step(i, n, t), r =>
            {
                dlg.Close();
                if (r.Ok) { prepared = true; then(); }
                else Dialogs.Problems(this, "✕ Can't go on yet", new ArrayList { r.Error ?? new Dictionary<string, object> { { "text", r.ErrorText } } }, true, r.Log);
            });
            dlg.Stop += () => running.Stop();
            dlg.ShowDialog(this);
        }

        // ---------- 03 Model ----------
        static readonly string[][] ClothRows = {
            new[] { "colour", "Color", "The painted colors" },
            new[] { "normal", "Normal", "Small surface detail (a tangent-space normal map, OpenGL style)" },
            new[] { "roughness", "Roughness", "Shiny (black) to matte (white); a combined Metallic-Roughness image is fine" },
            new[] { "metallic", "Metallic", "Optional: white where it is metal. Empty: no metal" } };
        static readonly string[][] SkinRows = {
            new[] { "colour", "Color", "The skin colors" },
            new[] { "normal", "Normal", "Optional: skin detail. Empty: smooth" },
            new[] { "roughness", "Roughness", "Optional. Empty: the game's usual skin value" } };

        void PageModel(int inner)
        {
            body.Add(U.H1("Model"));
            body.Add(U.Sub("Your model replaces Amicia's whole body in every chapter: clothes, skin, head and hands. The game's own pieces are always hidden; only the game's eyes and lashes stay."));
            PartBox(inner, cloth, "Cloth", "Everything that is not skin: clothes, boots, belts. Drawn with the game's cloth shading.", ClothRows);
            PartBox(inner, skin, "Skin", "Your head, hands and any other skin, with eyelids weighted to the face bones. Drawn with the game's skin shading.", SkinRows);
            body.Add(U.Hint("Texture size: the size of your images, square: 1024, 2048 or 4096 (the largest). Each part can have its own size. Larger images are sharper but make the mod bigger and the build slower."));
            body.Add(U.Banner("Talking scenes: the game's lip movement belongs to its own head, so your head's mouth moves only with the face bones it is weighted to.", inner));
            body.Add(U.H2("Checks"));
            if (cloth.File == null || skin.File == null) body.Add(U.Check("", "The checks run when both parts are given.", inner));
            else ChecksView("model", inner, () => RunCheck("model", new Dictionary<string, object> { { "model", ModelChoices() } }));
            FootL(U.BtnHint("Back", "Previous page", false, (s, e) => Show(PrevPage("model"))), ExportBtn());
            // the release without the blend file (user, 2026-10-31) has no Optimized Kit: no button for it
            if (HasOptKit()) FootL(U.BtnHint("Open the Optimized Kit", "Start here: Jga7's optimized Blender kit (OptimizedKit.blend)", false, (s, e) => OpenKit("0 Optimized Kit")));
            FootL(U.BtnHint("Open the clean slate folder", "Amicia Clean slate + weight body: the skeleton and the weighted body to model on, with an example", false, (s, e) => OpenKit("Amicia Clean slate + weight body")),
                 U.BtnHint("Open Amicia Parts", "The game's original pieces, to look at only", false, (s, e) => OpenKit("Amicia Parts")),
                 U.BtnHint("Open the textures folder", "Amicia Seamless Textures: tiling skin, cloth, leather and metal made from your own game (never shared)", false, (s, e) => OpenKit("Amicia Seamless Textures")),
                 AdvBtn());
            Button next;
            FootR(U.BtnHint("Next", StepAfter("model"), true, (s, e) => Show(NextPage("model")), out next));
            next.Enabled = cloth.File != null && skin.File != null && checkState != "running" && TexSizesOk();
        }
        string StepAfter(string p) { var f = Flow(); int i = f.IndexOf(StepOf[p]); return i + 1 < f.Count ? f[i + 1] : ""; }

        bool TexSizesOk()
        {
            foreach (var p in new[] { cloth, skin })
                foreach (var kv in p.Images) { var s = U.PngSize(kv.Value); if (s.Width > 4096 || s.Height > 4096) return false; }
            return true;
        }

        void PartBox(int inner, Part part, string name, string hint, string[][] rows)
        {
            body.Add(U.H2(name));
            body.Add(U.Hint(hint));
            var drop = new DropBox("Drop your " + name + " model here", "glTF file (.glb or .gltf), weighted to the kit's skeleton", "glTF model (*.glb;*.gltf)|*.glb;*.gltf", inner);
            if (part.File != null) drop.Filled("✓ " + PartSummary(part), "glTF file (.glb or .gltf), weighted to the kit's skeleton");
            drop.Dropped += f =>
            {
                part.File = f; part.Info = null; part.Images.Clear(); checkState = "";
                Bg(new[] { "inspect", f }, null, r =>
                {
                    if (!r.Ok) { part.File = null; Dialogs.Problems(this, "✕ Can't use this file", new ArrayList { r.Error ?? new Dictionary<string, object> { { "text", r.ErrorText } } }, true, r.Log); }
                    else part.Info = r.Json;
                    Refresh("model");
                });
                Refresh("model");
            };
            body.Add(drop);
            if (part.File != null) body.Add(TexTable(inner, part, rows, "model"));
        }

        string PartSummary(Part p)
        {
            if (p.Info == null) return Path.GetFileName(p.File) + " · reading…";
            int ts = p.TexSize;
            return Path.GetFileName(p.File) + " · " + J.N(p.Info, "parts") + " part" + (J.N(p.Info, "parts") == 1 ? "" : "s") + " · " +
                   J.N(p.Info, "vertices").ToString("N0") + " vertices · " + (ts > 0 ? "textures " + ts : "no color image yet");
        }

        Control TexTable(int inner, Part part, string[][] rows, string pg)
        {
            var t = new TableLayoutPanel { ColumnCount = 3, AutoSize = true, Width = inner, Margin = new Padding(0, 4, 0, 8), CellBorderStyle = TableLayoutPanelCellBorderStyle.None };
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, inner * 55 / 100));
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, inner * 30 / 100));
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, inner * 15 / 100));
            var tex = J.D(part.Info, "textures");
            foreach (var r in rows)
            {
                string key = r[0];
                var name = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, Margin = new Padding(0, 2, 0, 2) };
                name.Controls.Add(U.Text(r[1], Theme.Bold, Theme.Ink, 0, 0));
                var h = U.Hint(r[2]); h.MaximumSize = new Size(inner * 55 / 100 - 10, 0); name.Controls.Add(h);
                Label st;
                string mine;
                string act = "Choose image…";
                if (part.Images.TryGetValue(key, out mine)) { st = U.Text("✓ " + Path.GetFileName(mine) + " (your image)", Theme.Bold, Theme.Ok); act = "Replace…"; }
                else if (J.D(tex, key) != null) { st = U.Text("✓ found in your file", Theme.Bold, Theme.Ok); act = "Replace…"; }
                else st = U.Text("not found", Theme.Bold, Theme.Warn);
                string k2 = key;
                var b = U.Small(act, (s, e) =>
                {
                    using (var d = U.FileDialog(new OpenFileDialog { Filter = "PNG image (*.png)|*.png", Title = r[1] }))
                    {
                        if (d.ShowDialog(this) != DialogResult.OK) return;
                        var sz = U.PngSize(d.FileName);
                        if (sz.Width == 0) { Dialogs.Problems(this, "✕ Can't use this image", Item("Your " + r[1].ToLower() + " image is not a PNG file.", "Save it as PNG."), true, null); return; }
                        if (sz.Width > 4096 || sz.Height > 4096) { Dialogs.Problems(this, "✕ Can't build yet", Item("Your " + r[1].ToLower() + " image is " + sz.Width + " x " + sz.Height + ". The largest size is 4096.", "Save it at 4096 x 4096 (or 2048, 1024)."), true, null); return; }
                        part.Images[k2] = d.FileName;
                        checkState = "";
                        Refresh(pg);
                    }
                });
                t.Controls.Add(name); t.Controls.Add(st); t.Controls.Add(b);
            }
            return t;
        }
        static ArrayList Item(string text, string advice) { return new ArrayList { new Dictionary<string, object> { { "text", text }, { "advice", advice } } }; }

        Dictionary<string, object> PartChoice(Part p)
        {
            var d = new Dictionary<string, object> { { "file", p.File } };
            foreach (var kv in p.Images) d[kv.Key] = kv.Value;
            if (p.Overlay != null) d["overlay"] = p.Overlay;
            return d;
        }
        Dictionary<string, object> ModelChoices() { return new Dictionary<string, object> { { "cloth", PartChoice(cloth) }, { "skin", PartChoice(skin) } }; }

        Dictionary<string, object> HairChoices()
        {
            var d = new Dictionary<string, object> { { "file", hairFile } };
            if (hairRgba != null) d["rgba"] = hairRgba;
            else { if (hairColour != null) d["colour"] = hairColour; if (hairAlpha != null) d["alpha"] = hairAlpha; }
            if (hairNormal != null) d["normal"] = hairNormal;
            return d;
        }

        public Dictionary<string, object> AllChoices()
        {
            var ch = new Dictionary<string, object>();
            if (Groups.Count < 4) ch["groups"] = Groups.ToList();
            if (wantModel) ch["model"] = ModelChoices();
            if (wantHair && hairFile != null) ch["hair"] = HairChoices();
            if (wantEyes && eyeColour != null) ch["eyes"] = new Dictionary<string, object> { { "colour", eyeColour } };
            return ch;
        }

        string WriteChoices(Dictionary<string, object> ch)
        {
            Directory.CreateDirectory(Paths.MakerData);
            string p = Path.Combine(Paths.MakerData, "choices.json");
            if (Groups.Count < 4 && !ch.ContainsKey("groups")) ch["groups"] = Groups.ToList();
            File.WriteAllText(p, EngineRun.Json.Serialize(ch));
            return p;
        }

        // The engine's check of what the page has (check-model, the hair check): notes never block, errors do.
        // The Model and Hair pages share these fields; checkFor says whose result they hold (session 67: the Hair page
        // showed the Model's finished check and never checked the hair; its problems first appeared at Build).
        void RunCheck(string who, Dictionary<string, object> ch)
        {
            if (demo) return;
            checkFor = who; checkState = "running";
            // One check at a time: every check rebuilds data\inputs, so a second one started while the first runs
            // wiped the first one's files (session 67: FileNotFoundError ...\checks\model.txt). A newer request
            // waits and replaces any older waiting one; it starts when the running check ends.
            if (checkBusy) { checkNext = () => RunCheck(who, ch); return; }
            checkBusy = true;
            Bg(new[] { "check", WriteChoices(ch), "--data", Paths.MakerData }, null, r =>
            {
                checkBusy = false;
                if (checkNext != null) { var go = checkNext; checkNext = null; go(); return; }   // superseded
                if (checkFor != who) return;   // the other page started its own check meanwhile
                if (r.Json == null) { checkState = "failed"; checkFatal = new ArrayList { new Dictionary<string, object> { { "text", r.ErrorText } } }; checkNotes = new ArrayList(); }
                else { checkState = "done"; checkFatal = J.A(r.Json, "fatal") ?? new ArrayList(); checkNotes = J.A(r.Json, "notes") ?? new ArrayList(); }
                Refresh(page);
            });
        }

        void ChecksView(string who, int inner, Action start)
        {
            if (checkFor != who && !demo) { checkState = ""; checkFatal = checkNotes = null; }
            if (checkState == "")
            {
                if (!demo) { start(); }
                else { checkState = "done"; }
            }
            if (checkState == "running") { body.Add(U.Check("", who == "hair" ? "Checking your hair…" : "Checking your model… (about a minute)", inner)); return; }
            if (checkFatal != null && checkFatal.Count > 0)
                body.Add(U.Row(U.Check("bad", "✕ " + checkFatal.Count + " problem" + (checkFatal.Count == 1 ? "" : "s") + " to fix before building", inner - 90),
                               U.Small("Show", (s, e) => Dialogs.Problems(this, "✕ Can't build yet", checkFatal, true, null))));
            else body.Add(U.Check("ok", "✓ Fits in every chapter · Skeleton: every bone found", inner));
            if (checkNotes != null && checkNotes.Count > 0)
                body.Add(U.Row(U.Check("warn", "! " + checkNotes.Count + " thing" + (checkNotes.Count == 1 ? "" : "s") + " to know", inner - 90),
                               U.Small("Show", (s, e) => Dialogs.Problems(this, "! Something to know", checkNotes, false, null))));
        }

        // ---------- 04 Hair ----------
        void PageHair(int inner)
        {
            var title = U.Row(U.H1("Hair"), new Box("optional", Theme.Pill, Theme.Mute, 84, Theme.Small) { Margin = new Padding(8, 10, 0, 0) });
            body.Add(title);
            body.Add(U.Sub("Your hairstyle replaces the game's whole hair. Without your own hair, the game's hair stays."));
            body.Add(U.H3("Hair model"));
            body.Add(Drop(inner, "Drop your hair model here", "glTF file (.glb or .gltf), weighted to the kit's head and hair bones", "glTF model (*.glb;*.gltf)|*.glb;*.gltf",
                hairFile, f =>
                {
                    hairFile = f; hairInfo = null; checkState = "";
                    Bg(new[] { "inspect", f }, null, r => { if (r.Ok) hairInfo = r.Json; else { hairFile = null; Dialogs.Problems(this, "✕ Can't use this file", new ArrayList { r.Error ?? new Dictionary<string, object> { { "text", r.ErrorText } } }, true, r.Log); } Refresh("hair"); });
                }, hairFile != null && hairInfo != null ? Path.GetFileName(hairFile) + " · " + J.N(hairInfo, "vertices").ToString("N0") + " vertices" : null));
            body.Add(U.H3("Hair images"));
            int half = (inner - 20) / 2;
            var row = U.Row(
                Labeled("Color", Drop(half, "Drop the color image here", "PNG painted on your hair's UVs", "PNG image (*.png)|*.png", hairColour, f => { hairColour = f; hairRgba = null; checkState = ""; Refresh("hair"); }, null)),
                Labeled("Alpha", Drop(half, "Drop the alpha image here", "PNG, white = hair, black = see-through", "PNG image (*.png)|*.png", hairAlpha, f => { hairAlpha = f; hairRgba = null; checkState = ""; Refresh("hair"); }, null)));
            body.Add(row);
            body.Add(U.Row(U.Hint("Or one image with both:"), U.Small(hairRgba != null ? "✓ " + Path.GetFileName(hairRgba) : "Choose one RGBA image…", (s, e) =>
            {
                using (var d = U.FileDialog(new OpenFileDialog { Filter = "PNG image (*.png)|*.png", Title = "One RGBA image" }))
                    if (d.ShowDialog(this) == DialogResult.OK) { hairRgba = d.FileName; hairColour = hairAlpha = null; checkState = ""; Refresh("hair"); }
            }), U.Hint("PNG whose transparency is the alpha")));
            body.Add(Labeled("Normal (optional)", Drop(half, "Drop the normal image here", "Strand detail. Empty: smooth hair", "PNG image (*.png)|*.png", hairNormal, f => { hairNormal = f; checkState = ""; Refresh("hair"); }, null)));
            body.Add(U.Banner("In Chapter I the hair does not swing: that chapter has no hair bones.", inner));
            bool imagesOk = hairRgba != null || (hairColour != null && hairAlpha != null);
            if (hairFile != null)
            {
                if (!imagesOk) body.Add(U.Check("warn", "! Give the color and alpha images (or one RGBA image) to use your hair.", inner));
                else
                {
                    body.Add(U.H2("Checks"));
                    ChecksView("hair", inner, () => RunCheck("hair", new Dictionary<string, object> { { "hair", HairChoices() } }));
                }
                body.Add(U.Small("Remove my hair (keep the game's hair)", (s, e) => { hairFile = hairColour = hairAlpha = hairRgba = hairNormal = null; checkState = ""; Refresh("hair"); }));
            }
            FootL(U.BtnHint("Back", "Previous page", false, (s, e) => Show(PrevPage("hair"))), ExportBtn(),
                 U.BtnHint("Open the hair kit folder", "The hair kit: skeleton, bald head, the game's hair", false, (s, e) => OpenKit("hair_kit")), AdvBtn());
            Button next;
            FootR(U.BtnHint("Next", StepAfter("hair"), true, (s, e) => Show(NextPage("hair")), out next));
            next.Enabled = (hairFile == null || (imagesOk && checkState != "running")) && (hairFile == null || hairInfo != null);
        }

        Control Labeled(string label, Control c)
        {
            var p = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, Margin = new Padding(0, 0, 20, 0) };
            p.Controls.Add(U.Text(label, Theme.Bold, Theme.Ink, 6, 0));
            p.Controls.Add(c);
            return p;
        }

        DropBox Drop(int width, string title, string hint, string filter, string current, Action<string> set, string filledText)
        {
            var d = new DropBox(title, hint, filter, width);
            if (current != null) { d.File = current; d.Filled("✓ " + (filledText ?? Path.GetFileName(current)), hint); }
            d.Dropped += f => set(f);
            return d;
        }

        // ---------- 06 Eyes ----------
        void PageEyes(int inner)
        {
            body.Add(U.H1("Eyes"));
            body.Add(U.Sub("The eye color: one image, on the game's eyes (the only game piece the tool keeps). The eyes' shape does not change."));
            body.Add(Labeled("Eye color", Drop((inner - 20) / 2, "Drop your eye color image here", "PNG painted on the head kit's iris image, same size", "PNG image (*.png)|*.png", eyeColour, f =>
            {
                var s = U.PngSize(f);
                if (s.Width != 512 || s.Height != 512)
                {
                    Dialogs.Problems(this, "✕ Can't use this image", Item("Your eye color image " + (s.Width == 0 ? "is not a PNG file." : "is " + s.Width + " x " + s.Height + ": it must be the size of the kit's iris image (512 x 512)."),
                        "Paint on the kit's image without resizing it."), true, null);
                    eyeColour = null;
                }
                else eyeColour = f;
                Refresh("eyes");
            }, null)));
            FootL(U.BtnHint("Back", "Previous page", false, (s, e) => Show(PrevPage("eyes"))),
                 U.BtnHint("Open the head kit folder", "The iris image to paint on is in it", false, (s, e) => OpenKit("head_kit")), AdvBtn());
            Button next;
            FootR(U.BtnHint("Next", "Build", true, (s, e) => Show("build"), out next));
            next.Enabled = eyeColour != null;
        }

        // ---------- the kits (made from the player's own game files the first time) ----------
        // shipped in engine\data\optimized_kit, already in the kits, or a development run (no engine\data: kits_src)
        static bool HasOptKit()
        {
            return !Directory.Exists(Paths.EngineData) || File.Exists(Path.Combine(Paths.EngineData, @"optimized_kit\OptimizedKit.blend"))
                || Directory.Exists(Path.Combine(Paths.KitsDir, "0 Optimized Kit"));
        }
        void OpenKit(string name)
        {
            if (demo) return;
            string folder = Path.Combine(Paths.KitsDir, name);
            if (File.Exists(Path.Combine(Paths.KitsDir, "kits_ready.txt")) && Directory.Exists(folder)) { U.Open(folder); return; }
            Prepare(() =>
            {
                var dlg = new ProgressDialog("Making the kits", "The first time, the kits are made from your own game files (a few minutes).");
                Bg(new[] { "kits", "--data", Paths.MakerData }, (i, n, t) => dlg.Step(i, n, t), r =>
                {
                    dlg.Close();
                    // the first time: the kits folder itself, the Optimized Kit selected (first in the list; user, 2026-10-31)
                    string opt = Path.Combine(Paths.KitsDir, "0 Optimized Kit");
                    if (r.Ok && J.B(r.Json, "made") && Directory.Exists(opt)) System.Diagnostics.Process.Start("explorer.exe", "/select,\"" + opt + "\"");
                    else if (r.Ok && Directory.Exists(folder)) U.Open(folder);
                    else Dialogs.Problems(this, "✕ The kits could not be made", new ArrayList { r.Error ?? new Dictionary<string, object> { { "text", r.ErrorText } } }, true, r.Log);
                });
                dlg.Stop += () => running.Stop();
                dlg.ShowDialog(this);
            });
        }
        public void RebuildKits()
        {
            try { if (File.Exists(Path.Combine(Paths.KitsDir, "kits_ready.txt"))) File.Delete(Path.Combine(Paths.KitsDir, "kits_ready.txt")); } catch (Exception) { }
            OpenKit("Amicia Clean slate + weight body");
        }

        // ---------- 07 Build ----------
        TextBox fName, fVersion, fDesc;
        string modName = "", modVersion = "1.0", modDesc = "", previewFile;
        ProgressBar bar;
        Label barText;
        DateTime buildStart;

        void PageBuild(int inner)
        {
            body.Add(U.H1("Build your mod"));
            body.Add(U.Sub("The tool makes your mod: a small folder and .zip with only your changes. Your game is not changed."));
            body.Add(U.H2("About your mod"));
            body.Add(U.Hint("Shown to players on the \"Install a mod\" page."));
            fName = U.Field(modName, 360); fVersion = U.Field(modVersion, 90); fDesc = U.Field(modDesc, Math.Min(620, inner - 180));
            fDesc.MaxLength = 300; fName.MaxLength = 60; fVersion.MaxLength = 20;
            fName.TextChanged += (s, e) => modName = fName.Text; fVersion.TextChanged += (s, e) => modVersion = fVersion.Text; fDesc.TextChanged += (s, e) => modDesc = fDesc.Text;
            body.Add(FieldRow("Name", fName, "Empty: \"Amicia mod\""));
            body.Add(FieldRow("Version", fVersion, "Raise it when you share a new build of the same mod"));
            body.Add(FieldRow("Description", fDesc, "What your mod changes, in your own words (one or two lines)"));
            body.Add(FieldRow("Preview picture (optional)", Drop(Math.Min(620, inner - 180), "Drop a picture here", "PNG or JPG, e.g. a screenshot of your mod", "Picture (*.png;*.jpg;*.jpeg)|*.png;*.jpg;*.jpeg", previewFile, f => { previewFile = f; Refresh("build"); }, null), null));
            body.Add(U.H2("What goes into it"));
            body.Add(WhatTable(inner));
            body.Add(U.Hint("About " + EstimateMb() + " MB (an estimate). No game files inside: the changes are applied to each player's own game files when they install it."));
            body.Add(U.Hint("Free disk space needed: about 4 GB while building (free now: " + FreeGb(Paths.Home) + " GB)."));
            if (cloth.TexSize >= 4096 || skin.TexSize >= 4096)
                body.Add(U.Check("warn", "! 4096 textures take long to build: about 25 minutes per game file on a slower PC (" + GameFiles().Count + " game files).", inner));
            if (buildState == "ready")
            {
                Button b;
                body.Add(U.Row(U.BtnHint("Build my mod", "Makes the mod folder and its .zip", true, (s, e) => StartBuild(), out b)));
                b.Enabled = !demo || true;
            }
            else if (buildState == "building")
            {
                body.Add(U.H2("Building…"));
                bar = new ProgressBar { Width = inner, Height = 12, Maximum = 1000, Margin = new Padding(0, 6, 0, 4) };
                body.Add(bar);
                barText = U.Hint(buildText);
                body.Add(U.Row(barText, U.Small("Stop", (s, e) => { if (running != null) running.Stop(); })));
            }
            else if (buildState == "done")
            {
                body.Add(U.H2("Done"));
                body.Add(U.Check("ok", buildResultText, inner));
                body.Add(U.Hint("Share the zip. Players install it with this tool (\"Install a mod\" on the first page)."));
                body.Add(U.Row(U.BtnHint("Open the mod folder", "Shows the folder and the zip in Explorer", false, (s, e) => U.Open(Path.GetDirectoryName(lastModFolder))),
                               U.BtnHint("Back to the first page", "", false, (s, e) => { buildState = "ready"; Show("home"); })));
            }
            else
            {
                body.Add(U.Check("bad", buildResultText, inner));
                body.Add(U.Row(U.Small("Show details", (s, e) => Dialogs.Text(this, "Build details", buildLog)), U.Small("Try again", (s, e) => { buildState = "ready"; Refresh("build"); })));
            }
            FootL(U.BtnHint("Back", PrevPage("build") == "home" ? "" : Flow()[Flow().Count - 2], false, (s, e) => { if (buildState != "building") Show(PrevPage("build")); }), AdvBtn());
        }

        Control FieldRow(string label, Control field, string hint)
        {
            var t = new TableLayoutPanel { ColumnCount = 2, AutoSize = true, Margin = new Padding(0, 2, 0, 2) };
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 180));
            t.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            t.Controls.Add(U.Text(label, Theme.Bold, Theme.Ink, 6, 0));
            var p = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, Margin = new Padding(0) };
            p.Controls.Add(field);
            if (hint != null) p.Controls.Add(U.Hint(hint));
            t.Controls.Add(p);
            return t;
        }

        Control WhatTable(int inner)
        {
            var t = new TableLayoutPanel { ColumnCount = 3, AutoSize = true, Width = inner, Margin = new Padding(0, 4, 0, 6) };
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 120));
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, inner - 260));
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 140));
            foreach (var h in new[] { "Change", "From", "Chapters" }) t.Controls.Add(U.Text(h, Theme.Bold, Theme.Mute));
            string ch = Groups.Count == 4 ? "all" : string.Join(", ", new[] { "chp1", "chp2", "main", "final" }.Where(g => Groups.Contains(g)).Select(GroupName));
            if (wantModel)
            {
                t.Controls.Add(U.Text("Model", Theme.Body, Theme.Ink));
                t.Controls.Add(U.Text("Cloth: " + FileName(cloth.File) + SizeOf(cloth) + " · Skin: " + FileName(skin.File) + SizeOf(skin), Theme.Body, Theme.Ink));
                t.Controls.Add(U.Text(ch, Theme.Body, Theme.Ink));
            }
            if (wantHair && hairFile != null)
            {
                t.Controls.Add(U.Text("Hair", Theme.Body, Theme.Ink));
                t.Controls.Add(U.Text(FileName(hairFile) + (hairRgba != null ? " + one RGBA image" : " + color and alpha images") + (hairNormal != null ? " + normal" : ""), Theme.Body, Theme.Ink));
                t.Controls.Add(U.Text("all", Theme.Body, Theme.Ink));
            }
            if (wantEyes && eyeColour != null)
            {
                t.Controls.Add(U.Text("Eyes", Theme.Body, Theme.Ink));
                t.Controls.Add(U.Text(FileName(eyeColour), Theme.Body, Theme.Ink));
                t.Controls.Add(U.Text(ch, Theme.Body, Theme.Ink));
            }
            return t;
        }
        static string FileName(string f) { return f == null ? "—" : Path.GetFileName(f); }
        static string SizeOf(Part p) { return p.TexSize > 0 ? " (" + p.TexSize + ")" : ""; }
        public static string GroupName(string g) { return g == "chp1" ? "Chapter I" : g == "chp2" ? "Chapter II" : g == "main" ? "Main chapters" : "Final chapters"; }

        List<string> GameFiles()
        {
            var f = new List<string>();
            if (wantModel || (wantEyes && eyeColour != null))
            {
                if (Groups.Contains("chp1")) f.Add("P_AMICIA_CHP1");
                if (Groups.Contains("chp2")) f.Add("P_AMICIA_CHP2");
                if (Groups.Contains("main")) { f.Add("P_AMICIA"); f.Add("P_AMICIA_UNIVERSITY"); }
                if (Groups.Contains("final")) f.Add("P_AMICIA_END");
            }
            if (wantHair && hairFile != null) foreach (var x in new[] { "P_AMICIA", "P_AMICIA_CHP1", "P_AMICIA_CHP2", "P_AMICIA_END", "P_AMICIA_UNIVERSITY", "CUSTOMIZATION" }) if (!f.Contains(x)) f.Add(x);
            return f;
        }

        // Measured: the Fanta mod (two parts at 2048 in 5 game files + hair + iris) is 24 MB.
        int EstimateMb()
        {
            double mb = 0;
            int n = GameFiles().Count(x => x != "CUSTOMIZATION");
            if (wantModel) foreach (var p in new[] { cloth, skin }) { double s = Math.Max(p.TexSize, 1024) / 2048.0; mb += 1.8 * s * s * n; }
            if (wantHair && hairFile != null) mb += 6;
            if (wantEyes && eyeColour != null) mb += 0.2;
            return Math.Max(1, (int)Math.Round(mb));
        }
        static string FreeGb(string path)
        {
            try { return (new DriveInfo(Path.GetPathRoot(Path.GetFullPath(path))).AvailableFreeSpace / 1073741824.0).ToString("0.0"); } catch (Exception) { return "?"; }
        }

        void StartBuild()
        {
            if (demo) return;
            if (wantModel && (cloth.File == null || skin.File == null)) { Show("model"); return; }
            if (wantHair && hairFile != null && hairRgba == null && (hairColour == null || hairAlpha == null)) { Show("hair"); return; }
            var ch = AllChoices();
            if (!ch.ContainsKey("model") && !ch.ContainsKey("hair") && !ch.ContainsKey("eyes"))
            {
                Dialogs.Problems(this, "✕ Can't build yet", Item("Nothing to build: give a model, hair or an eye color image.", "Go back and add your files."), true, null);
                return;
            }
            string name = modName.Trim() == "" ? "Amicia mod" : modName.Trim();
            string version = modVersion.Trim() == "" ? "1.0" : modVersion.Trim();
            string preview = null;
            if (previewFile != null)
            {
                try { preview = PreviewPng(previewFile); }
                catch (Exception e) { Dialogs.Problems(this, "✕ Can't use this picture", Item("The preview picture cannot be read: " + e.Message, "Use a PNG or JPG file."), true, null); return; }
            }
            buildState = "building"; buildText = "Starting…"; buildLog = "";
            buildStart = DateTime.Now;
            Show("build");
            string choices = WriteChoices(ch);
            var changes = new List<string>();
            if (ch.ContainsKey("model")) changes.Add("Model");
            if (ch.ContainsKey("hair")) changes.Add("Hair");
            if (ch.ContainsKey("eyes")) changes.Add("Eyes");
            Prepare(() => Bg(new[] { "build", choices, "--data", Paths.MakerData }, (i, n, t) => BuildProgress(0, 0.9, i, n, t), r =>
            {
                buildLog = r.Log;
                if (r.Stopped) { BuildFailed("The build was stopped. Your game was not changed."); return; }
                if (!r.Ok)
                {
                    var fatal = J.A(r.Json, "fatal");
                    if (fatal != null && fatal.Count > 0) { BuildFailed("The build stopped: your files need a fix first. Your game was not changed."); Dialogs.Problems(this, "✕ Can't build yet", fatal, true, null); return; }
                    BuildFailed("The build stopped: " + (r.ErrorText ?? "unknown reason") + ". Your game was not changed.");
                    return;
                }
                var args = new List<string> { "make-mod", "--data", Paths.MakerData, "--name", name, "--version", version, "--description", modDesc.Trim(), "--out", Paths.ModsOut, "--changes", string.Join(",", changes) };
                if (Groups.Count < 4) { args.Add("--groups"); args.Add(string.Join(",", Groups)); }
                if (preview != null) { args.Add("--preview"); args.Add(preview); }
                args.Add("--tool-page"); args.Add(Program.Nexus);   // named in the mod's README (user, 2026-10-30)
                Directory.CreateDirectory(Paths.ModsOut);
                Bg(args.ToArray(), (i, n, t) => BuildProgress(0.9, 0.1, i, n, t), r2 =>
                {
                    buildLog += r2.Log;
                    if (r2.Stopped) { BuildFailed("The build was stopped. Your game was not changed."); return; }
                    if (!r2.Ok) { BuildFailed("The build stopped: " + (r2.ErrorText ?? "unknown reason") + ". Your game was not changed."); return; }
                    lastModFolder = J.S(r2.Json, "folder"); lastModName = name;
                    string zip = J.S(r2.Json, "zip");
                    long size = File.Exists(zip) ? new FileInfo(zip).Length : 0;
                    buildState = "done";
                    buildResultText = "✓ Your mod is ready (" + U.Mb(size) + ").\nFolder: " + lastModFolder + "\nZip: " + zip;
                    Show("build");
                    Success("The mod has been built successfully", name + " (" + U.Mb(size) + ") is ready to install. It is in " + lastModFolder + ", with a .zip next to it to share.", lastModFolder);
                });
            }));
        }

        void BuildProgress(double start, double share, int i, int n, string text)
        {
            double frac = start + share * Math.Max(0, i - 1) / Math.Max(1, n);
            string left = "";
            double secs = (DateTime.Now - buildStart).TotalSeconds;
            if (frac > 0.05) { double rest = secs / frac * (1 - frac); left = " · about " + (rest >= 3600 ? (int)(rest / 3600) + " h " : "") + Math.Max(1, (int)(rest % 3600 / 60)) + " min left"; }
            buildText = text + " (step " + i + " of " + n + ")" + left;
            if (bar != null && !bar.IsDisposed) { bar.Value = Math.Min(1000, (int)(frac * 1000)); barText.Text = buildText; }
        }

        void BuildFailed(string text) { buildState = "failed"; buildResultText = text; Show("build"); }

        // The preview stored as PNG, at most 1280 px wide (§0.1 item 7): re-encoded here, so no file is passed on as it came.
        static string PreviewPng(string src)
        {
            using (var img = Image.FromFile(src))
            {
                int w = Math.Min(1280, img.Width), h = Math.Max(1, img.Height * w / img.Width);
                using (var bmp = new Bitmap(w, h, PixelFormat.Format24bppRgb))
                {
                    using (var g = Graphics.FromImage(bmp)) { g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic; g.DrawImage(img, 0, 0, w, h); }
                    Directory.CreateDirectory(Paths.MakerData);
                    string p = Path.Combine(Paths.MakerData, "preview.png");
                    bmp.Save(p, ImageFormat.Png);
                    return p;
                }
            }
        }

        // ---------- 08 Install a mod ----------
        void PageInstall(int inner)
        {
            body.Add(U.H1("Install a mod"));
            body.Add(U.Sub("Put a mod into your game, or put the original files back."));
            body.Add(U.Banner("Mods made with " + Program.ToolName + " are installed with this tool: a mod has no installer of its own.", inner));
            body.Add(U.H3("Mod"));
            var d = Drop(inner, "Drop a mod folder or its .zip here", "A mod made with this tool", "Mod (*.zip;mod.json)|*.zip;mod.json", modPath, f => LoadMod(f), modJson != null ? J.S(modJson, "name") : null);
            d.DragEnter += (s, e) => e.Effect = DragDropEffects.Copy;
            body.Add(d);
            var row = U.Row(U.BtnHint("Browse…", "Choose a mod folder or .zip", false, (s, e) =>
            {
                using (var o = U.FileDialog(new OpenFileDialog { Filter = "Mod (*.zip;mod.json)|*.zip;mod.json", Title = "Choose a mod (.zip, or mod.json in a mod folder)" }))
                    if (o.ShowDialog(this) == DialogResult.OK) LoadMod(o.FileName);
            }));
            if (lastModFolder != null) row.Controls.Add(U.BtnHint("The mod you just built: " + lastModName, "Uses the last mod from \"Make a mod\"", false, (s, e) => LoadMod(lastModFolder)));
            body.Add(row);
            if (modJson != null) body.Add(ModCard(inner));
            body.Add(U.H3("Game"));
            body.Add(GameLine(installGameKind, installGameText, true));
            if (installStateText != "") body.Add(U.Check(installStateKind, installStateText, inner));
            Button bi, br;
            body.Add(U.Row(U.BtnHint("Install", "Saves your original files, then puts the mod in", true, (s, e) => DoInstall(false), out bi),
                           U.BtnHint("Restore", "Puts your original files back", false, (s, e) => DoRestore(), out br)));
            bool busy = installMsgKind == "busy";
            bi.Enabled = modJson != null && game != null && installGameKind == "ok" && !busy;
            br.Enabled = game != null && installState != null && J.D(installState, "installed") != null && !busy;
            if (game != null && !Writable(Path.Combine(game, "DATAS")))
                body.Add(U.Hint("Windows asks for permission once, because this game is inside Program Files. Tip: a Steam library outside Program Files needs no permission (Steam: Settings › Storage › Move)."));
            if (installMsg != null) body.Add(U.Check(busy ? "" : installMsgKind, installMsg, inner));
            body.Add(U.Hint("Installing makes the new game files from your own files first (about a minute); every file is checked before it is copied in."));
            body.Add(U.Banner("One Amicia mod at a time: installing another Amicia mod replaces the one installed.", inner));
            FootL(U.BtnHint("Back", "Return to the first page", false, (s, e) => { if (installMsgKind != "busy") Show("home"); }));
        }

        static bool Writable(string dir)
        {
            try { string p = Path.Combine(dir, ".amicia_tool_test"); File.WriteAllBytes(p, new byte[0]); File.Delete(p); return true; }
            catch (Exception) { return false; }
        }

        Control ModCard(int inner)
        {
            var p = new Panel { Width = inner, Height = 100, Margin = new Padding(0, 8, 0, 8), BackColor = Theme.Card };
            p.Paint += (s, e) => { using (var pen = new Pen(Theme.Line)) e.Graphics.DrawRectangle(pen, 0, 0, p.Width - 1, p.Height - 1); };
            var pic = new PictureBox { Location = new Point(12, 10), Size = new Size(120, 80), SizeMode = PictureBoxSizeMode.Zoom, BackColor = Theme.Acc2 };
            if (modPreview != null) pic.Image = modPreview;
            p.Controls.Add(pic);
            var groups = J.A(modJson, "groups");
            string ch = groups == null || groups.Count == 0 || groups.Count == 4 ? "all chapters" : string.Join(", ", groups.Cast<object>().Select(g => GroupName(g.ToString())));
            var changes = J.A(modJson, "changes");
            string what = changes != null && changes.Count > 0 ? "Changes " + string.Join(", ", changes.Cast<object>()) + " · " : "";
            var files = J.A(modJson, "files");
            var l = U.Text(J.S(modJson, "name") + "   version " + J.S(modJson, "version"), Theme.Bold, Theme.Ink);
            l.Location = new Point(146, 10);
            var dsc = U.Text(J.S(modJson, "description") ?? "", Theme.Body, Theme.Ink); dsc.Location = new Point(146, 34); dsc.MaximumSize = new Size(inner - 160, 36);
            var h = U.Hint(what + ch + " · " + (files != null ? files.Count : 0) + " game files"); h.Location = new Point(146, 72);
            p.Controls.Add(l); p.Controls.Add(dsc); p.Controls.Add(h);
            return p;
        }

        // A mod folder, its mod.json or its .zip: only read here (the engine checks it again before installing).
        void LoadMod(string path)
        {
            modJson = null; modPreview = null; installMsg = null;
            try
            {
                string folder = path;
                if (path.EndsWith("mod.json", StringComparison.OrdinalIgnoreCase)) folder = Path.GetDirectoryName(path);
                if (path.EndsWith(".zip", StringComparison.OrdinalIgnoreCase))
                {
                    using (var z = ZipFile.OpenRead(path))
                    {
                        var mj = z.Entries.FirstOrDefault(e => e.Name == "mod.json");
                        if (mj == null) throw new InvalidDataException("no mod.json");
                        using (var r = new StreamReader(mj.Open())) modJson = EngineRun.Json.Deserialize<Dictionary<string, object>>(r.ReadToEnd());
                        var pv = z.Entries.FirstOrDefault(e => e.Name == "preview.png");
                        if (pv != null) using (var s = pv.Open()) { var ms = new MemoryStream(); s.CopyTo(ms); ms.Position = 0; modPreview = Image.FromStream(ms); }
                    }
                    modPath = path;
                }
                else
                {
                    modJson = EngineRun.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(folder, "mod.json")));
                    string pv = Path.Combine(folder, "preview.png");
                    if (File.Exists(pv)) using (var s = File.OpenRead(pv)) { var ms = new MemoryStream(); s.CopyTo(ms); ms.Position = 0; modPreview = Image.FromStream(ms); }
                    modPath = folder;
                }
                if (J.S(modJson, "character") != "amicia" || J.A(modJson, "files") == null) throw new InvalidDataException("not a mod");
            }
            catch (Exception)
            {
                modJson = null; modPath = null;
                installMsgKind = "bad"; installMsg = "✕ This is not a mod made with this tool.";
            }
            Refresh("install");
        }

        static readonly Dictionary<string, string> InstallMsgs = new Dictionary<string, string> {
            { "steam_updated", "! Steam updated the game: the installed mod is no longer fully installed. Nothing was changed: press Restore first." },
            { "unsupported", "✕ Unsupported version of the game: only the Steam version is supported. Nothing was changed." },
            { "bad_mod", "✕ This is not a mod made with this tool." },
            { "needs_newer", "✕ This mod needs a newer version of " + "{TOOL}" + "." },
            { "busy", "! The tool is already installing or restoring: wait for it to finish." } };

        void InstallAnswer(EngineResult r, string okText, Action again)
        {
            if (r.Ok) { installMsgKind = "ok"; installMsg = okText; SuccessFor(okText); }
            else if (J.D(r.Json, "confirm_replace") != null)
            {
                var old = J.D(r.Json, "confirm_replace");
                installMsg = null; installMsgKind = null;
                if (Dialogs.ConfirmReplace(this, J.S(old, "name") + " " + J.S(old, "version"), J.S(modJson, "name"))) { again(); return; }
            }
            else if (J.B(r.Json, "needs_elevation")) { Elevated(J.S(r.Json, "plan"), okText); return; }
            else
            {
                string code = r.ErrorCode, m;
                installMsgKind = code == "game_running" || code == "steam_updated" || code == "busy" ? "warn" : "bad";
                installMsg = code != null && InstallMsgs.TryGetValue(code, out m) ? m.Replace("{TOOL}", Program.ToolName)
                    : "✕ " + (r.ErrorText ?? "Something went wrong.") + (r.Advice != null ? " " + Cap(r.Advice) + "." : "");
            }
            CheckGame(game, true);
        }
        static string Cap(string s) { return s.Length > 0 ? char.ToUpper(s[0]) + s.Substring(1) : s; }

        // A clear pop-up when a build, an install or a restore finished (user, 2026-10-30). Not in the self-test or
        // the pictures: a modal window would stop them. Shown after the page has redrawn.
        void Success(string title, string text, string folder = null)
        {
            if (selfTesting || demo) return;
            BeginInvoke((Action)(() => Dialogs.Success(this, title, text, folder)));
        }
        void SuccessFor(string okText)
        {
            if (okText.Contains("Installed")) Success("The mod has been installed successfully", "Start the game as usual. To go back to the original Amicia, use Restore on this page.");
            else if (okText.Contains("restored")) Success("The original files are back", "Amicia's original game files were restored and checked.");
        }

        void DoInstall(bool replace)
        {
            installMsgKind = "busy"; installMsg = "Installing… (about a minute)";
            Refresh("install");
            var args = new List<string> { "install", modPath, game, "--data", Paths.ModsData };
            if (replace) args.Add("--replace");
            Bg(args.ToArray(), (i, n, t) => { installMsg = "Installing… " + t; Refresh("install"); }, r => InstallAnswer(r, "✓ Installed and checked. Start the game as usual.", () => DoInstall(true)));
        }

        void DoRestore()
        {
            installMsgKind = "busy"; installMsg = "Putting your original files back…";
            Refresh("install");
            Bg(new[] { "restore", game, "--data", Paths.ModsData }, null, r => InstallAnswer(r, "✓ Original files restored and checked.", DoRestore));
        }

        // The game folder needs permission: the tool starts itself elevated with /apply <plan> (one Windows prompt).
        void Elevated(string plan, string okText)
        {
            string res = plan + ".result.json";
            try { if (File.Exists(res)) File.Delete(res); } catch (Exception) { }
            var t = new Thread(() =>
            {
                string msg = null, kind = "ok";
                try
                {
                    var p = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(Application.ExecutablePath, "/apply " + EngineRun.Quote(plan)) { Verb = "runas", UseShellExecute = true });
                    p.WaitForExit();
                    var d = File.Exists(res) ? EngineRun.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(res)) : null;
                    if (J.B(d, "ok")) msg = okText;
                    else { kind = "bad"; msg = "✕ " + (J.S(J.D(d, "error"), "text") ?? "The copy into the game folder did not finish.") + " Nothing was changed."; }
                }
                catch (System.ComponentModel.Win32Exception e)
                {
                    kind = "warn";
                    msg = e.NativeErrorCode == 1223 ? "! Permission was not given: nothing was changed." : "✕ " + e.Message;
                    new EngineRun().Run(new[] { "restore", game, "--data", Paths.ModsData }, null);   // clears the unfinished state (nothing was copied)
                }
                BeginInvoke((Action)(() => { installMsgKind = kind; installMsg = msg; CheckGame(game, true); if (kind == "ok") SuccessFor(okText); }));
            });
            t.IsBackground = true;
            t.Start();
        }

        // ---------- tests: the whole flow on a fake game (JGA_STEAM, JGA_HOME), driven through the pages' own code ----------
        // spec: a JSON file {"cloth": {...}, "skin": {...}, "hair": {...}, "eyes": "<png>", "name": "...", "log": "<txt>",
        // "shots": "<dir>"}. Steps: Make a mod (detect, prepare, inputs, check, build, make-mod), then Install a mod
        // (the mod just built: install, then restore). Every step and its result go to the log; exit code 0 = all passed.
        public void SelfTest(string specPath)
        {
            selfTesting = true;
            var spec = EngineRun.Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(specPath));
            string logPath = J.S(spec, "log"), shots = J.S(spec, "shots");
            Directory.CreateDirectory(shots);
            Action<string> log = t => File.AppendAllText(logPath, DateTime.Now.ToString("HH:mm:ss") + " " + t + Environment.NewLine);
            Action<string> snap = n => { using (var b = new Bitmap(Width, Height)) { DrawToBitmap(b, new Rectangle(0, 0, Width, Height)); b.Save(Path.Combine(shots, n + ".png"), ImageFormat.Png); } };
            int step = 0, fails = 0;
            DateTime t0 = DateTime.Now, since = DateTime.Now;
            Func<string, bool, bool> check = (what, ok) => { log((ok ? "OK   " : "FAIL ") + what); if (!ok) fails++; return ok; };
            var timer = new System.Windows.Forms.Timer { Interval = 1000 };
            Action next = () => { step++; since = DateTime.Now; };
            Action<int> finish = code => { timer.Stop(); log("--- end: " + fails + " failures, " + (DateTime.Now - t0).TotalMinutes.ToString("0.0") + " min"); Environment.Exit(code); };
            Load += (s, e) => { log("--- selftest start"); Show("home"); timer.Start(); };
            timer.Tick += (s, e) =>
            {
                double waited = (DateTime.Now - since).TotalMinutes;
                if (waited > 90) { check("step " + step + " did not finish within 90 min", false); finish(1); return; }
                switch (step)
                {
                    case 0: wantModel = wantHair = wantEyes = true; Show("what"); DetectGame(false); next(); break;
                    case 1:
                        if (gameKind == "wait") return;
                        check("What to change: game found: " + gameText, gameKind == "ok"); snap("s02_what");
                        if (gameKind != "ok") { finish(1); return; }
                        next();
                        Prepare(() => { log("prepare done"); Show("model"); step = 3; since = DateTime.Now; });
                        break;
                    case 2: return;   // prepare running (its dialog)
                    case 3:
                        cloth.File = J.S(J.D(spec, "cloth"), "file"); skin.File = J.S(J.D(spec, "skin"), "file");
                        foreach (var kv in J.D(spec, "cloth")) if (kv.Key != "file") cloth.Images[kv.Key] = (string)kv.Value;
                        foreach (var kv in J.D(spec, "skin")) if (kv.Key != "file") skin.Images[kv.Key] = (string)kv.Value;
                        cloth.Info = skin.Info = null;
                        Bg(new[] { "inspect", cloth.File }, null, r => { cloth.Info = r.Json; Bg(new[] { "inspect", skin.File }, null, r2 => { skin.Info = r2.Json; checkState = ""; Show("model"); }); });
                        next(); break;
                    case 4:
                        if (cloth.Info == null || skin.Info == null || checkState != "done") return;
                        check("Model: inspect " + PartSummary(cloth) + " / " + PartSummary(skin), J.B(cloth.Info, "ok") && J.B(skin.Info, "ok"));
                        check("Model: check: " + (checkFatal == null ? 0 : checkFatal.Count) + " problems, " + (checkNotes == null ? 0 : checkNotes.Count) + " notes", checkFatal != null && checkFatal.Count == 0);
                        snap("s03_model");
                        var h = J.D(spec, "hair");
                        hairFile = J.S(h, "file"); hairRgba = J.S(h, "rgba"); hairColour = J.S(h, "colour"); hairAlpha = J.S(h, "alpha");
                        hairInfo = null;   // checkState is NOT reset here: the Hair page must start its own check (session 67)
                        Bg(new[] { "inspect", hairFile }, null, r => { hairInfo = r.Json; Show("hair"); });
                        next(); break;
                    case 5:
                        if (hairInfo == null || checkFor != "hair" || checkState != "done") return;
                        check("Hair: check: " + (checkFatal == null ? 0 : checkFatal.Count) + " problems, " + (checkNotes == null ? 0 : checkNotes.Count) + " notes", checkFatal != null && checkFatal.Count == 0);
                        snap("s04_hair");
                        eyeColour = J.S(spec, "eyes"); Show("eyes");
                        check("Eyes: image accepted (512 x 512)", U.PngSize(eyeColour).Width == 512);
                        modName = J.S(spec, "name") ?? "Selftest mod"; modDesc = "Made by the app's self-test (session 65).";
                        Show("build"); snap("s07_build_ready");
                        StartBuild(); next(); break;
                    case 6:
                        if (buildState == "building") { if ((DateTime.Now - since).TotalSeconds < 2) snap("s07_build_running"); return; }
                        snap("s07_build_end");
                        if (!check("Build: " + buildResultText.Replace("\n", " | "), buildState == "done")) { File.WriteAllText(logPath + ".build.txt", buildLog); finish(1); return; }
                        Show("install"); installGameKind = "wait"; DetectGame(true); LoadMod(lastModFolder); next(); break;
                    case 7:
                        if (installGameKind == "wait") return;
                        check("Install page: mod card " + J.S(modJson, "name") + ", game: " + installGameText + ", state: " + installStateText, modJson != null && installGameKind == "ok");
                        snap("s08_install_ready");
                        DoInstall(false); next(); break;
                    case 8:
                        if (installMsgKind == "busy" || installMsgKind == null) return;
                        snap("s08_installed");
                        check("Install: " + installMsg, installMsgKind == "ok" && installMsg.Contains("Installed and checked"));
                        installMsgKind = null; installMsg = null;
                        Bg(new[] { "state", game, "--data", Paths.ModsData }, null, r => { installState = r.Json; log("state after install: " + EngineRun.Json.Serialize(J.D(r.Json, "files")));
                            check("state: every file of the mod is 'ours'", J.D(r.Json, "files") != null && J.D(r.Json, "files").Values.All(v => (string)v == "ours"));
                            DoRestore(); step = 10; since = DateTime.Now; });
                        next(); break;
                    case 9: return;
                    case 10:
                        if (installMsgKind == "busy" || installMsgKind == null) return;
                        snap("s08_restored");
                        check("Restore: " + installMsg, installMsgKind == "ok" && installMsg.Contains("restored"));
                        Bg(new[] { "state", game, "--data", Paths.ModsData }, null, r =>
                        {
                            check("state after restore: all original, nothing installed", J.D(r.Json, "files") != null && J.D(r.Json, "files").Values.All(v => (string)v == "original") && J.D(r.Json, "installed") == null);
                            finish(fails == 0 ? 0 : 1);
                        });
                        next(); break;
                }
            };
        }

        // ---------- tests: every page as a PNG (no engine calls) ----------
        public void Shots(string dir)
        {
            demo = true;
            Directory.CreateDirectory(dir);
            StartPosition = FormStartPosition.Manual;
            Location = new Point(-3000, -3000);
            ShowInTaskbar = false;
            base.Show();
            Application.DoEvents();
            Action<string> snap = name =>
            {
                Application.DoEvents();
                using (var b = new Bitmap(Width, Height)) { DrawToBitmap(b, new Rectangle(0, 0, Width, Height)); b.Save(Path.Combine(dir, name + ".png"), ImageFormat.Png); }
            };
            Size = new Size(1060, 860);
            Show("home"); snap("01_home");
            game = @"D:\SteamLibrary\steamapps\common\A Plague Tale Innocence"; gameKind = "ok"; gameText = "✓ Game found: " + game + " · Steam version";
            wantModel = true; wantHair = true; wantEyes = true;
            Show("what"); snap("02_what");
            cloth.File = @"C:\Users\you\Documents\cloth.glb"; skin.File = @"C:\Users\you\Documents\skin.glb";
            var tex = new Dictionary<string, object> { { "colour", new Dictionary<string, object> { { "size", new ArrayList { 4096, 4096 } } } }, { "normal", new Dictionary<string, object>() }, { "roughness", new Dictionary<string, object>() } };
            cloth.Info = new Dictionary<string, object> { { "parts", 1 }, { "vertices", 38104 }, { "textures", tex } };
            var tex2 = new Dictionary<string, object> { { "colour", new Dictionary<string, object> { { "size", new ArrayList { 2048, 2048 } } } }, { "normal", new Dictionary<string, object>() } };
            skin.Info = new Dictionary<string, object> { { "parts", 1 }, { "vertices", 21377 }, { "textures", tex2 } };
            checkState = "done"; checkFatal = new ArrayList();
            checkNotes = new ArrayList { new Dictionary<string, object> { { "text", "3 vertices use bones that Chapter I does not have (left hood). They move with the nearest bone there." }, { "advice", "fine for small pieces; nothing to do." } },
                                         new Dictionary<string, object> { { "text", "In Chapter I your model should include the forearms: part of the arm is not covered." }, { "advice", "model the forearms (or long sleeves). Chapter I's own hands end at the wrist." } } };
            Show("model"); snap("03_model");
            scroll.AutoScrollPosition = new Point(0, 2000); snap("03_model_bottom");
            checkState = ""; checkNotes = null;
            Show("hair"); snap("04_hair");
            eyeColour = @"C:\Users\you\Documents\iris.png";
            Show("eyes"); snap("06_eyes");
            hairFile = @"C:\Users\you\Documents\hair.glb"; hairRgba = @"C:\Users\you\Documents\hair.png";
            Show("build"); snap("07_build");
            buildState = "building"; buildText = "building P_AMICIA_CHP2.DPC (step 3 of 6) · about 1 h 20 min left"; Show("build"); bar.Value = 420; scroll.AutoScrollPosition = new Point(0, 2000); snap("07_build_running");
            buildState = "done"; buildResultText = "✓ Your mod is ready (24.1 MB).\nFolder: C:\\Users\\you\\Amicia Mods\\My mod\nZip: C:\\Users\\you\\Amicia Mods\\My mod.zip"; Show("build"); scroll.AutoScrollPosition = new Point(0, 2000); snap("07_build_done");
            lastModFolder = @"C:\Users\you\Amicia Mods\My mod"; lastModName = "My mod";
            modJson = new Dictionary<string, object> { { "name", "My mod" }, { "version", "1.0" }, { "description", "A long hooded coat and a braid." }, { "character", "amicia" },
                                                       { "changes", new ArrayList { "Model", "Hair" } }, { "files", new ArrayList { 1, 2, 3, 4, 5, 6 } } };
            modPath = lastModFolder; installGameKind = "ok"; installGameText = "✓ Game: " + game + " · Steam version"; installStateText = "✓ No mod installed: original game files";
            installState = new Dictionary<string, object>();
            installMsgKind = "ok"; installMsg = "✓ Installed and checked. Start the game as usual.";
            Show("install"); snap("08_install");
            var adv = new AdvancedForm(this); adv.Show(); Application.DoEvents();
            using (var b = new Bitmap(adv.Width, adv.Height)) { adv.DrawToBitmap(b, new Rectangle(0, 0, adv.Width, adv.Height)); b.Save(Path.Combine(dir, "09_advanced.png"), ImageFormat.Png); }
            adv.Close();
            foreach (var kv in new Dictionary<string, Form> { { "10_export_help", Dialogs.ExportHelpForm() }, { "12_about", Dialogs.AboutForm() },
                                                               { "11_notes", Dialogs.ProblemsForm("! Something to know", checkNotesDemo(), false, null) },
                                                               { "11_replace", Dialogs.ReplaceForm("Old mod 1.2", "My mod") } })
            {
                var f = kv.Value; f.StartPosition = FormStartPosition.Manual; f.Location = new Point(0, 0); f.Show(); Application.DoEvents();
                using (var b = new Bitmap(f.Width, f.Height)) { f.DrawToBitmap(b, new Rectangle(0, 0, f.Width, f.Height)); b.Save(Path.Combine(dir, kv.Key + ".png"), ImageFormat.Png); }
                f.Close();
            }
        }
        static ArrayList checkNotesDemo()
        {
            return new ArrayList { new Dictionary<string, object> { { "text", "In Chapter I your model should include the forearms: part of the arm is not covered." }, { "advice", "model the forearms (or long sleeves). Chapter I's own hands end at the wrist." } },
                                   new Dictionary<string, object> { { "text", "3 vertices use bones that Chapter I does not have (left hood). They move with the nearest bone there." }, { "advice", "fine for small pieces; nothing to do." } } };
        }

        public Part Cloth { get { return cloth; } }
        public Part Skin { get { return skin; } }
        public void AfterAdvanced() { checkState = ""; Show(page); }
        public void WorkDeleted() { prepared = false; checkState = ""; }
    }
}
