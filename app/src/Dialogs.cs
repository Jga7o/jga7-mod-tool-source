// M17 session 4: pop-ups (mockup 11), How to export (10), About (12), Advanced (09) and the progress window.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Windows.Forms;

namespace JgaModTool
{
    public static class Dialogs
    {
        static Form Base(string title, int w)
        {
            var f = new Form { Text = title, Width = w, FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false, MinimizeBox = false,
                               StartPosition = FormStartPosition.CenterParent, Font = Theme.Body, ShowInTaskbar = false, AutoScaleMode = AutoScaleMode.None };
            Theme.Dark(f);
            return f;
        }

        // A dialog: a scrolling column of controls and a button row at the bottom right.
        static Form Make(string title, int w, Action<Column> fill, params Button[] buttons)
        {
            var f = Base(title, w);
            var col = new Column { Inner = w - 70, Location = new Point(22, 16) };
            fill(col);
            var scroll = new Panel { Dock = DockStyle.Fill, AutoScroll = true };
            scroll.Controls.Add(col);
            var foot = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 52, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(12, 10, 12, 8), BackColor = Theme.Foot };
            foreach (var b in buttons) { b.Margin = new Padding(8, 0, 0, 0); foot.Controls.Add(b); }
            f.Controls.Add(scroll); f.Controls.Add(foot);
            col.PerformLayout();
            f.ClientSize = new Size(w - 16, Math.Min(640, col.PreferredSize.Height + 36 + 52));
            if (buttons.Length > 0) f.AcceptButton = buttons[0];
            return f;
        }
        static Button Close(string t, bool primary, DialogResult r)
        {
            var b = U.Btn(t, primary, null);
            b.DialogResult = r;
            return b;
        }

        // Notes ("! Something to know", never block) and errors ("✕ Can't build yet").
        public static Form ProblemsForm(string title, ArrayList items, bool bad, string log)
        {
            var buttons = new List<Button> { Close("OK", true, DialogResult.OK) };
            Form f = null;
            if (log != null)
            {
                var d = U.Btn("Show details", false, (s, e) => Text(f, "Details", log));
                buttons.Add(d);
            }
            f = Make(title, 620, col =>
            {
                var h = U.Text(title, Theme.H2, bad ? Theme.Bad : Theme.Ink, 0, 8);
                col.Add(h);
                foreach (var o in items)
                {
                    var d = o as Dictionary<string, object>;
                    string text = J.S(d, "text") ?? (o != null ? o.ToString() : "");
                    string part = J.S(d, "part");
                    if (part == "model" || part == "hair") text = (part == "model" ? "Model: " : "Hair: ") + text;
                    col.Add(U.Text(text, Theme.Body, Theme.Ink, 6, 0));
                    string adv = J.S(d, "advice");
                    if (!string.IsNullOrEmpty(adv)) col.Add(U.Text("Advice: " + adv, Theme.Small, Theme.Mute, 0, 6));
                }
            }, buttons.ToArray());
            return f;
        }
        public static void Problems(IWin32Window owner, string title, ArrayList items, bool bad, string log)
        {
            using (var f = ProblemsForm(title, items, bad, log)) f.ShowDialog(owner);
        }

        // "✓ The mod has been built successfully" and the like (user, 2026-10-30).
        public static Form SuccessForm(string title, string text, string folder = null)
        {
            var buttons = new List<Button> { Close("OK", true, DialogResult.OK) };
            if (folder != null)   // the mod folder is selected in Explorer, the .zip next to it (user: "I couldn't find my mod")
                buttons.Add(U.Btn("Open the folder", false, (s, e) => System.Diagnostics.Process.Start("explorer.exe", "/select,\"" + folder + "\"")));
            return Make(title, 560, col =>
            {
                col.Add(U.Text("✓ " + title, Theme.H2, Theme.Ok, 0, 8));
                col.Add(U.Text(text, Theme.Body, Theme.Ink));
            }, buttons.ToArray());
        }
        public static void Success(IWin32Window owner, string title, string text, string folder = null)
        {
            using (var f = SuccessForm(title, text, folder)) f.ShowDialog(owner);
        }

        public static Form ReplaceForm(string oldMod, string newMod)
        {
            return Make("This replaces " + oldMod, 560, col =>
            {
                col.Add(U.Text("This replaces " + oldMod, Theme.H2, Theme.Ink, 0, 8));
                col.Add(U.Text(oldMod + " is installed: one Amicia mod at a time. Its files are put back to the originals first, then " + newMod + " is installed.", Theme.Body, Theme.Ink));
            }, Close("Replace it", true, DialogResult.OK), Close("Cancel", false, DialogResult.Cancel));
        }
        public static bool ConfirmReplace(IWin32Window owner, string oldMod, string newMod)
        {
            using (var f = ReplaceForm(oldMod, newMod)) return f.ShowDialog(owner) == DialogResult.OK;
        }

        static readonly string[] Steps = {
            "In Blender, select your model's object(s) and the kit's skeleton (YOUR_armature).",
            "File › Export › glTF 2.0 (.glb/.gltf).",
            "Format: glTF Binary (.glb) (or glTF Separate (.gltf + .bin + textures)).",
            "Include › Limit to: Selected Objects.",
            "Transform › +Y Up: on.",
            "Data › Mesh: Apply Modifiers on, UVs on, Normals on, Tangents on, Vertex Colors off.",
            "Data › Material: Materials Export, Images Automatic (keep your images PNG).",
            "Data › Shape Keys: off.",
            "Data › Armature: Use Rest Position on.",
            "Data › Skinning: on, Bone Influences 4, Include All Bone Influences off.",
            "Data › Compression: off.",
            "Animation: off.",
            "Export. Then drop the file on the page." };

        public static Form ExportHelpForm()
        {
            return Make("How to export your model", 680, col =>
            {
                col.Add(U.Text("How to export your model", Theme.H2, Theme.Ink, 0, 2));
                col.Add(U.Hint("The steps for Blender (tested with Blender 5.0)."));
                for (int i = 0; i < Steps.Length; i++) col.Add(U.Text((i + 1) + ".  " + Steps[i], Theme.Body, Theme.Ink, 3, 0));
                col.Add(U.Text("Textures: in Blender's Principled BSDF, connect your images to Base Color, Normal (through a Normal Map node), Roughness and Metallic. The exporter puts them in the file; the tool finds them there. One material per part.", Theme.Small, Theme.Mute, 12, 4));
                col.Add(U.Text("Weights: every vertex must be weighted to the kit's bones. If some are not, Blender adds a bone named \"neutral_bone\" and the tool stops; find them with Select › Select All by Trait › Ungrouped Vertices.", Theme.Small, Theme.Mute, 4, 4));
            }, Close("OK", true, DialogResult.OK));
        }
        public static void ExportHelp(IWin32Window owner) { using (var f = ExportHelpForm()) f.ShowDialog(owner); }

        public static Form AboutForm()
        {
            return Make("About", 620, col =>
            {
                col.Add(U.Text(Program.ToolName, Theme.H1, Theme.Ink, 0, 2));
                col.Add(U.Sub("Makes and installs mods for Amicia in A Plague Tale: Innocence."));
                col.Add(U.Text("Made by " + Program.Author, Theme.Bold, Theme.Ink, 2, 4));
                col.Add(U.BtnHint("Gumroad page", "Opens the tool's Gumroad page in your browser", false, (s, e) => U.Open(Program.Gumroad)));
                col.Add(U.BtnHint("Nexus Mods page", "Opens the tool's Nexus Mods page in your browser", false, (s, e) => U.Open(Program.Nexus)));
                col.Add(U.Text("This tool was created with the help of AI.", Theme.Body, Theme.Ink, 8, 4));
                col.Add(U.Text("Version " + Program.Version + " · supported game: the Steam version only (checked by file hashes)", Theme.Body, Theme.Ink, 8, 4));
                col.Add(U.Hint("Unofficial fan-made tool. Not made or supported by the game's developers or publisher. Use at your own risk."));
                col.Add(U.Hint("Each mod's README names the tool and links its page; nothing else about the tool goes into the mod."));
            }, Close("Close", true, DialogResult.OK));
        }
        public static void About(IWin32Window owner) { using (var f = AboutForm()) f.ShowDialog(owner); }

        public static void Text(IWin32Window owner, string title, string text)
        {
            using (var f = Base(title, 820))
            {
                f.ClientSize = new Size(800, 520);
                var t = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Both, Dock = DockStyle.Fill, Font = new Font("Consolas", 9f), BackColor = Theme.Card, ForeColor = Theme.Ink, Text = (text ?? "").Replace("\r\n", "\n").Replace("\n", "\r\n"), WordWrap = false };
                f.Controls.Add(t);
                f.ShowDialog(owner);
            }
        }
    }

    // A long engine step (prepare, kits): a bar, the current step, and Stop.
    public class ProgressDialog : Form
    {
        ProgressBar bar = new ProgressBar { Maximum = 1000, Width = 460, Height = 14 };
        Label step;
        public event Action Stop;
        bool closing;

        public ProgressDialog(string title, string text)
        {
            Text = title; FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = MinimizeBox = false; ControlBox = false;
            StartPosition = FormStartPosition.CenterParent; Theme.Dark(this); Font = Theme.Body; ShowInTaskbar = false; ClientSize = new Size(520, 170);
            var col = new Column { Inner = 470, Location = new Point(24, 16) };
            col.Add(U.Text(text, Theme.Body, Theme.Ink, 0, 8));
            col.Add(bar);
            step = U.Hint("Starting…");
            col.Add(step);
            var stop = U.Small("Stop", (s, e) => { if (Stop != null) Stop(); });
            col.Add(stop);
            Controls.Add(col);
        }
        public void Step(int i, int n, string t)
        {
            if (IsDisposed) return;
            bar.Value = Math.Min(1000, Math.Max(0, (i - 1) * 1000 / Math.Max(1, n)));
            step.Text = t + " (step " + i + " of " + n + ")";
        }
        public new void Close() { if (!closing && !IsDisposed) { closing = true; base.Close(); } }
    }

    // Advanced (mockup 09): chapter groups, where the parts go, options, the work folder.
    public class AdvancedForm : Form
    {
        MainForm main;
        static readonly string[][] Groups = {
            new[] { "chp1", "Chapter I", "the opening chapter, with its own outfit" },
            new[] { "chp2", "Chapter II", "the second chapter, with its own outfit" },
            new[] { "main", "Main chapters", "most of the game: from the chapter after Chapter II until the final chapters" },
            new[] { "final", "Final chapters", "the end of the story, with Amicia's last outfit (it already appears before the last chapter)" } };
        static readonly string[][] Where = {
            new[] { "Chapter I", "P_AMICIA_CHP1.DPC", "C1-10", "C1-22 (hands)" },
            new[] { "Chapter II", "P_AMICIA_CHP2.DPC", "C2-26", "C2-16 (hands)" },
            new[] { "Main chapters", "P_AMICIA.DPC", "M38", "M42 (hand wrap)" },
            new[] { "Main chapters (second file)", "P_AMICIA_UNIVERSITY.DPC", "U-46", "U-40 (hand wrap)" },
            new[] { "Final chapters", "P_AMICIA_END.DPC", "E-38", "E-25 (hands)" } };

        public AdvancedForm(MainForm m)
        {
            main = m;
            Text = "Advanced"; StartPosition = FormStartPosition.CenterParent; Theme.Dark(this); Font = Theme.Body; ShowInTaskbar = false;
            ClientSize = new Size(900, 680); MinimizeBox = false;
            var scroll = new Panel { Dock = DockStyle.Fill, AutoScroll = true };
            int inner = 820;
            var col = new Column { Inner = inner, Location = new Point(24, 16) };
            col.Add(U.H1("Advanced"));
            col.Add(U.Sub("For people who know the game's files. Nothing here is needed for a normal mod."));
            col.Add(U.H2("Chapters"));
            col.Add(U.Hint("All ticked by default: the tool replaces Amicia's model in all chapters. Untick a group to leave those chapters unchanged. The game dresses Amicia differently in four groups of chapters:"));
            foreach (var g in Groups)
            {
                var t = U.Tick(g[1], g[2], main.Groups.Contains(g[0]), inner);
                string key = g[0];
                t.CheckedChanged += (s, e) => { if (t.Checked) main.Groups.Add(key); else main.Groups.Remove(key); };
                col.Add(t);
            }
            col.Add(U.Hint("Hair is always changed in all chapters: the game shares one hair texture between every chapter."));
            col.Add(U.H2("Where your parts go, per chapter group"));
            col.Add(U.Hint("Game words: \"game file\" = .DPC archive, \"piece\" = the game mesh slot your part is written into."));
            var tbl = new TableLayoutPanel { ColumnCount = 4, AutoSize = true, Width = inner };
            foreach (var h in new[] { "Chapters", "Game file", "Cloth goes into (cloth shading)", "Skin goes into (skin shading)" }) tbl.Controls.Add(U.Text(h, Theme.Bold, Theme.Mute, 2, 2));
            foreach (var r in Where) foreach (var c in r) tbl.Controls.Add(U.Text(c, Theme.Body, Theme.Ink, 2, 2));
            col.Add(tbl);
            col.Add(U.Hint("Each part keeps its own textures at your images' size (up to 4096) in every group. Every other game piece of Amicia is hidden, except the eyes and lashes."));
            col.Add(U.Hint("The hair textures are also written into CUSTOMIZATION.DPC (the outfit options' hair textures)."));
            col.Add(U.H2("Options"));
            col.Add(U.Text("Overlay map (optional; the game's own extra map; not transparency; leave empty)", Theme.Bold, Theme.Ink, 4, 0));
            col.Add(U.Hint("A grey image per part. Its meaning in the game is not known (perhaps dirt or wetness); empty uses the game's neutral value."));
            col.Add(U.Row(OverlayBtn("Cloth", main.Cloth), OverlayBtn("Skin", main.Skin)));
            col.Add(U.Text("Piece per part and chapter group: automatic", Theme.Bold, Theme.Ink, 8, 0));
            col.Add(U.Hint("Which game piece a part is written into (the table above)."));
            var keep = new CheckBox { Text = "Keep work files   · Faster rebuilds; uses about 3 GB", Checked = main.KeepWork, AutoSize = true, Margin = new Padding(0, 8, 0, 4) };
            keep.CheckedChanged += (s, e) => main.KeepWork = keep.Checked;
            col.Add(keep);
            col.Add(U.Row(
                U.BtnHint("Full check report", "Every check, with game file names", false, (s, e) => U.Open(Path.Combine(Paths.MakerData, @"inputs\checks"))),
                U.BtnHint("Build log", "Everything the last build did", false, (s, e) => U.Open(Path.Combine(Paths.MakerData, "logs"))),
                U.BtnHint("Open the work folder", "The tool's temporary files", false, (s, e) => { Directory.CreateDirectory(Paths.MakerData); U.Open(Paths.MakerData); }),
                U.BtnHint("Delete work files", "Frees disk space", false, (s, e) => DeleteWork()),
                U.BtnHint("Rebuild the kits", "Makes the kits again from your game", false, (s, e) => { Close(); main.RebuildKits(); })));
            scroll.Controls.Add(col);
            var foot = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 52, FlowDirection = FlowDirection.RightToLeft, Padding = new Padding(12, 10, 12, 8), BackColor = Theme.Foot };
            foot.Controls.Add(U.BtnHint("Close", "Back to the page you came from", true, (s, e) => Close()));
            Controls.Add(scroll); Controls.Add(foot);
            FormClosing += (s, e) =>
            {
                if (main.Groups.Count == 0) { MessageBox.Show(this, "Tick at least one chapter group.", Program.ToolName); e.Cancel = true; return; }
                main.AfterAdvanced();
            };
        }

        Control OverlayBtn(string label, Part p)
        {
            return U.BtnHint(label + ": " + (p.Overlay != null ? Path.GetFileName(p.Overlay) : "Choose image…"), "empty: the game's neutral value", false, (s, e) =>
            {
                using (var d = U.FileDialog(new OpenFileDialog { Filter = "PNG image (*.png)|*.png", Title = label + " overlay map" }))
                {
                    var r = d.ShowDialog(this);
                    if (r == DialogResult.OK) p.Overlay = d.FileName;
                    ((Button)s).Text = label + ": " + (p.Overlay != null ? Path.GetFileName(p.Overlay) : "Choose image…");
                }
            });
        }

        void DeleteWork()
        {
            string[] dirs = { Path.Combine(Paths.MakerData, "work"), Path.Combine(Paths.MakerData, "inputs"), Path.Combine(Paths.MakerData, "built"), Path.Combine(Paths.MakerData, "logs") };
            if (MessageBox.Show(this, "Delete the tool's work files? The next build reads your game files again (a few minutes). Your finished mods and your game are not touched.", Program.ToolName, MessageBoxButtons.OKCancel) != DialogResult.OK) return;
            foreach (var d in dirs) try { if (Directory.Exists(d)) Directory.Delete(d, true); } catch (Exception) { }
            main.WorkDeleted();
            MessageBox.Show(this, "Work files deleted.", Program.ToolName);
        }
    }
}
