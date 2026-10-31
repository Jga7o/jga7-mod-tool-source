// M17 session 4: the look of the mockups (output\app_mockups, v5): colours, text styles, drop boxes, check lines.
using System;
using System.Collections.Generic;
using System.Linq;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Windows.Forms;

namespace JgaModTool
{
    public static class Theme
    {
        // The G7 logo's colours (user, 2026-10-31): green and purple on black. Acc (purple) fills buttons, white text
        // on it; AccInk (neon green) is the accent as text or lines on the black surfaces.
        public static Color Bg = Color.FromArgb(0x07, 0x07, 0x08), Win = Color.FromArgb(0x0d, 0x0d, 0x0f), Ink = Color.FromArgb(0xe6, 0xe6, 0xea),
            Mute = Color.FromArgb(0x92, 0x8f, 0x9c), Line = Color.FromArgb(0x30, 0x2c, 0x3a), Acc = Color.FromArgb(0x5b, 0x2f, 0xc9),
            AccInk = Color.FromArgb(0x4c, 0xe0, 0x4c), Acc2 = Color.FromArgb(0x1f, 0x16, 0x38), Card = Color.FromArgb(0x18, 0x17, 0x1d),
            Ok = Color.FromArgb(0x5f, 0xd8, 0x6a), OkBg = Color.FromArgb(0x0f, 0x24, 0x13),
            Warn = Color.FromArgb(0xe3, 0xb0, 0x5a), WarnBg = Color.FromArgb(0x2b, 0x22, 0x12), Bad = Color.FromArgb(0xf0, 0x7a, 0x6e),
            BadBg = Color.FromArgb(0x33, 0x16, 0x16), Foot = Color.FromArgb(0x09, 0x09, 0x0b), Pill = Color.FromArgb(0x1f, 0x1c, 0x28),
            DropBg = Color.FromArgb(0x12, 0x11, 0x17), DropLine = Color.FromArgb(0x7a, 0x5c, 0xd6);

        // Windows 10 (2004+) / 11: a dark title bar to match (attribute 20; ignored where unsupported).
        [System.Runtime.InteropServices.DllImport("dwmapi.dll")]
        static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
        static Icon appIcon; static bool iconTried;
        static Icon AppIcon
        {
            get
            {
                if (!iconTried) { iconTried = true; try { appIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { } }
                return appIcon;
            }
        }
        public static void Dark(Form f)
        {
            f.BackColor = Win; f.ForeColor = Ink;
            if (AppIcon != null) f.Icon = AppIcon;
            f.HandleCreated += (s, e) => { int on = 1; try { DwmSetWindowAttribute(f.Handle, 20, ref on, 4); } catch (Exception) { } };
        }
        public static Font Body = new Font("Segoe UI", 10f), Small = new Font("Segoe UI", 8.75f), Bold = new Font("Segoe UI", 10f, FontStyle.Bold),
            H1 = new Font("Segoe UI", 16f, FontStyle.Bold), H2 = new Font("Segoe UI", 12f, FontStyle.Bold), H3 = new Font("Segoe UI", 10.5f, FontStyle.Bold),
            Big = new Font("Segoe UI", 17f, FontStyle.Bold);
    }

    // A page body: one column of controls, each as wide as the page.
    public class Column : FlowLayoutPanel
    {
        public Column()
        {
            FlowDirection = FlowDirection.TopDown;
            WrapContents = false;
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;
            BackColor = Theme.Win;
            Margin = new Padding(0);
        }
        public int Inner = 900;
        public T Add<T>(T c) where T : Control
        {
            var l = c as Label;
            if (l != null && l.AutoSize) { if (l.MaximumSize.Width == 0 || l.MaximumSize.Width > Inner) l.MaximumSize = new Size(Inner, 0); }
            else if (!(c is Label) && c.Dock == DockStyle.None && c.Width < 40) c.Width = Inner;
            Controls.Add(c);
            return c;
        }
    }

    public static class U
    {
        public static Label Text(string t, Font f, Color c, int top = 0, int bottom = 4)
        {
            return new Label { Text = t, Font = f, ForeColor = c, AutoSize = true, Margin = new Padding(0, top, 0, bottom), BackColor = Color.Transparent, UseMnemonic = false };
        }
        public static Label H1(string t) { return Text(t, Theme.H1, Theme.Ink, 0, 2); }
        public static Label H2(string t) { return Text(t, Theme.H2, Theme.Ink, 16, 4); }
        public static Label H3(string t) { return Text(t, Theme.H3, Theme.Ink, 10, 2); }
        public static Label Sub(string t) { return Text(t, Theme.Body, Theme.Mute, 0, 12); }
        public static Label Hint(string t) { return Text(t, Theme.Small, Theme.Mute, 2, 4); }

        public static Box Banner(string t, int width) { return new Box(t, Theme.Pill, Theme.Mute, width, Theme.Small); }
        public static Box Check(string kind, string t, int width)
        {
            Color bg = kind == "ok" ? Theme.OkBg : kind == "warn" ? Theme.WarnBg : kind == "bad" ? Theme.BadBg : Theme.Pill;
            Color fg = kind == "ok" ? Theme.Ok : kind == "warn" ? Theme.Warn : kind == "bad" ? Theme.Bad : Theme.Mute;
            return new Box(t, bg, fg, width, Theme.Body);
        }

        public static Button Btn(string t, bool primary, EventHandler click)
        {
            var b = new Button { Text = t, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Font = Theme.Body, FlatStyle = FlatStyle.Flat,
                                 Padding = new Padding(8, 3, 8, 3), Margin = new Padding(0), Cursor = Cursors.Hand, UseMnemonic = false };
            b.FlatAppearance.BorderColor = primary ? Theme.Acc : Theme.Line;
            b.BackColor = primary ? Theme.Acc : Theme.Card;
            b.ForeColor = primary ? Color.White : Theme.Ink;
            b.FlatAppearance.MouseOverBackColor = primary ? ControlPaint.Light(Theme.Acc, 0.2f) : Theme.Pill;
            if (click != null) b.Click += click;
            b.EnabledChanged += (s, e) => { b.BackColor = b.Enabled ? (primary ? Theme.Acc : Theme.Card) : Theme.Pill; b.ForeColor = b.Enabled ? (primary ? Color.White : Theme.Ink) : Theme.Mute; };
            return b;
        }
        public static Button Small(string t, EventHandler click)
        {
            var b = Btn(t, false, click);
            b.Font = Theme.Small;
            b.Padding = new Padding(4, 0, 4, 0);
            return b;
        }

        // A button with its one-line hint (the mockups' "bh"). Session 67 (user): no hint lines under the buttons (too
        // much small text); the hint shows when the mouse rests on the button.
        public static bool ShowHints = false;
        // Session 68 (user: "hover doesn't work yet"): one shared ToolTip stopped showing once the first page was
        // rebuilt. Now each window has its own ToolTip, the main window gets a fresh one per page (NewTips), tips show
        // even when the window is not the active one, and a disabled button shows its tip through its parent.
        static readonly Dictionary<Form, ToolTip> tipsOf = new Dictionary<Form, ToolTip>();
        static readonly Dictionary<Control, string> tipText = new Dictionary<Control, string>();
        static string manualText;
        static ToolTip TipsFor(Form f)
        {
            ToolTip t;
            if (tipsOf.TryGetValue(f, out t)) return t;
            t = DarkTips();
            tipsOf[f] = t;
            f.Disposed += (s, e) => { ToolTip x; if (tipsOf.TryGetValue(f, out x)) { tipsOf.Remove(f); x.Dispose(); } };
            return t;
        }
        public static void NewTips(Form f)
        {
            ToolTip t;
            if (tipsOf.TryGetValue(f, out t)) { tipsOf.Remove(f); t.Dispose(); }
            foreach (var c in tipText.Keys.ToList()) if (!c.IsDisposed && c.IsHandleCreated && c.FindForm() == f) Attach(c);
        }
        static ToolTip DarkTips()
        {
            var t = new ToolTip { InitialDelay = 400, ReshowDelay = 100, AutoPopDelay = 15000, OwnerDraw = true, ShowAlways = true };
            t.Popup += (s, e) =>
            {
                string text = e.AssociatedControl != null ? t.GetToolTip(e.AssociatedControl) : "";
                if (string.IsNullOrEmpty(text)) text = manualText ?? "";
                var z = TextRenderer.MeasureText(text, Theme.Small); e.ToolTipSize = new Size(z.Width + 16, z.Height + 10);
            };
            t.Draw += (s, e) =>
            {
                using (var b = new SolidBrush(Theme.Card)) e.Graphics.FillRectangle(b, e.Bounds);
                using (var p = new Pen(Theme.Acc)) e.Graphics.DrawRectangle(p, 0, 0, e.Bounds.Width - 1, e.Bounds.Height - 1);
                TextRenderer.DrawText(e.Graphics, e.ToolTipText, Theme.Small, new Point(8, 5), Theme.Ink);
            };
            return t;
        }
        public static void Tip(Control c, string text)
        {
            if (string.IsNullOrEmpty(text)) return;
            bool known = tipText.ContainsKey(c);
            tipText[c] = text;
            if (known) { Attach(c); return; }
            c.HandleCreated += (s, e) => Attach(c);
            c.Disposed += (s, e) => tipText.Remove(c);
            c.ParentChanged += (s, e) => WatchDisabled(c.Parent);
            if (c.IsHandleCreated) Attach(c);
            WatchDisabled(c.Parent);
        }
        static void Attach(Control c)
        {
            string text; var f = c.FindForm();
            if (f == null || f.IsDisposed || !tipText.TryGetValue(c, out text)) return;
            TipsFor(f).SetToolTip(c, text);
        }
        // Windows sends no mouse messages to a disabled control, but its parent gets them: show the tip from there.
        static readonly HashSet<Control> watched = new HashSet<Control>();
        static void WatchDisabled(Control parent)
        {
            if (parent == null || !watched.Add(parent)) return;
            Control shown = null;
            parent.Disposed += (s, e) => watched.Remove(parent);
            parent.MouseMove += (s, e) =>
            {
                var ch = parent.GetChildAtPoint(e.Location, GetChildAtPointSkip.Invisible);
                string text = null;
                if (ch != null && !ch.Enabled) tipText.TryGetValue(ch, out text);
                var f = parent.FindForm();
                if (f == null) return;
                if (text != null && ch != shown)
                {
                    shown = ch; manualText = text;
                    TipsFor(f).Show(text, parent, e.X + 12, e.Y + 20, 15000);
                }
                else if (text == null && shown != null) { shown = null; TipsFor(f).Hide(parent); }
            };
            parent.MouseLeave += (s, e) => { if (shown == null) return; shown = null; var f = parent.FindForm(); if (f != null) TipsFor(f).Hide(parent); };
        }
        public static FlowLayoutPanel BtnHint(string t, string hint, bool primary, EventHandler click, out Button btn)
        {
            var p = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
                                          Margin = new Padding(0, 2, 12, 2), BackColor = Color.Transparent };
            btn = Btn(t, primary, click);
            Tip(btn, hint);
            p.Controls.Add(btn);
            if (ShowHints && !string.IsNullOrEmpty(hint))
            {
                var h = Hint(hint);
                h.MaximumSize = new Size(200, 0);
                p.Controls.Add(h);
            }
            return p;
        }
        public static FlowLayoutPanel BtnHint(string t, string hint, bool primary, EventHandler click)
        {
            Button b;
            return BtnHint(t, hint, primary, click, out b);
        }

        public static FlowLayoutPanel Row(params Control[] cs)
        {
            var p = new FlowLayoutPanel { FlowDirection = FlowDirection.LeftToRight, WrapContents = true, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
                                          Margin = new Padding(0, 4, 0, 4), BackColor = Color.Transparent };
            foreach (var c in cs) if (c != null) p.Controls.Add(c);
            return p;
        }

        public static CheckBox Tick(string t, string hint, bool on, int width)
        {
            var c = new CheckBox { Text = t + (hint != null ? "   · " + hint : ""), Checked = on, Font = Theme.Body, AutoSize = false, Width = width, Height = 36,
                                   Padding = new Padding(10, 0, 0, 0), Margin = new Padding(0, 3, 0, 3), BackColor = on ? Theme.Acc2 : Theme.Card, ForeColor = Theme.Ink, UseMnemonic = false };
            c.CheckedChanged += (s, e) => c.BackColor = c.Checked ? Theme.Acc2 : Theme.Card;
            c.Paint += (s, e) => { using (var pen = new Pen(c.Checked ? Theme.AccInk : Theme.Line)) e.Graphics.DrawRectangle(pen, 0, 0, c.Width - 1, c.Height - 1); };
            return c;
        }

        public static TextBox Field(string t, int width)
        {
            return new TextBox { Text = t, Font = Theme.Body, Width = width, Margin = new Padding(0, 2, 0, 2), BorderStyle = BorderStyle.FixedSingle,
                                 BackColor = Theme.Card, ForeColor = Theme.Ink };
        }

        // Every file picker: opens where the last file came from; a matching file copied in Explorer (or its path copied
        // as text) is already filled in, so Open is enough (user, 2026-10-31: "copy paste a file instead of browsing").
        public static OpenFileDialog FileDialog(OpenFileDialog d)
        {
            if (DropBox.LastFolder != null && Directory.Exists(DropBox.LastFolder)) d.InitialDirectory = DropBox.LastFolder;
            string clip = DropBox.ClipboardPath();
            if (clip != null && File.Exists(clip) && DropBox.FilterTakes(d.Filter, clip)) d.FileName = clip;
            d.FileOk += (s, e) => { try { DropBox.LastFolder = Path.GetDirectoryName(d.FileName); } catch (Exception) { } };
            return d;
        }

        public static string Mb(long bytes) { return (bytes / 1048576.0).ToString(bytes < 10485760 ? "0.0" : "0") + " MB"; }

        public static void Open(string path)
        {
            try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true }); }
            catch (Exception e) { MessageBox.Show("Cannot open " + path + ": " + e.Message, Program.ToolName); }
        }

        // PNG header: (width, height), or (0, 0) when not a PNG.
        public static Size PngSize(string path)
        {
            try
            {
                using (var f = File.OpenRead(path))
                {
                    var b = new byte[24];
                    if (f.Read(b, 0, 24) < 24 || b[0] != 0x89 || b[1] != (byte)'P' || b[2] != (byte)'N' || b[3] != (byte)'G') return Size.Empty;
                    return new Size((b[16] << 24) | (b[17] << 16) | (b[18] << 8) | b[19], (b[20] << 24) | (b[21] << 16) | (b[22] << 8) | b[23]);
                }
            }
            catch (Exception) { return Size.Empty; }
        }
    }

    // A coloured line with padding (check lines, banners).
    public class Box : Label
    {
        public Box(string t, Color bg, Color fg, int width, Font f)
        {
            Text = t; BackColor = bg; ForeColor = fg; Font = f; AutoSize = true; UseMnemonic = false;
            MaximumSize = new Size(width, 0); MinimumSize = new Size(width, 0);
            Padding = new Padding(10, 6, 10, 6); Margin = new Padding(0, 4, 0, 4);
        }
    }

    // A drop box: drag a file onto it or click to choose one. Filled: a green box with the file's summary.
    public class DropBox : Panel
    {
        public string File;
        readonly string title, hint, filter;
        string filledText;
        public event Action<string> Dropped;
        Label lt, lh;
        LinkLabel other;

        public DropBox(string title, string hint, string filter, int width)
        {
            this.title = title; this.hint = hint; this.filter = filter;
            Width = width; Height = 64; Margin = new Padding(0, 6, 0, 4); AllowDrop = true; Cursor = Cursors.Hand; BackColor = Theme.DropBg;
            Padding = new Padding(4); ResizeRedraw = true;   // the docked labels stay inside the outline (else they hide it)
            lt = new Label { Font = Theme.Bold, ForeColor = Theme.Ink, AutoSize = false, TextAlign = ContentAlignment.MiddleCenter, Dock = DockStyle.Top, Height = 30, UseMnemonic = false };
            lh = new Label { Font = Theme.Small, ForeColor = Theme.Mute, AutoSize = false, TextAlign = ContentAlignment.TopCenter, Dock = DockStyle.Fill, UseMnemonic = false };
            other = new LinkLabel { Text = "Choose another file…", Font = Theme.Small, AutoSize = true, Visible = false, LinkColor = Theme.AccInk, ActiveLinkColor = Theme.Ink };
            other.Click += (s, e) => Choose();
            Controls.Add(lh); Controls.Add(lt); Controls.Add(other);
            // Paste (user, 2026-10-31: "copy paste a file instead of browsing"): copy the file in Explorer (or its path as
            // text), then Ctrl+V with the mouse over the box (MainForm), or right-click > Paste file.
            var menu = new ContextMenuStrip { BackColor = Theme.Card, ForeColor = Theme.Ink, ShowImageMargin = false };
            menu.Items.Add("Paste file  (Ctrl+V)", null, (s, e) => Paste());
            menu.Items.Add("Browse…", null, (s, e) => Choose());
            foreach (Control c in new Control[] { this, lt, lh })
            {
                c.MouseClick += (s, e) => { if (e.Button == MouseButtons.Left && File == null) Choose(); };
                c.ContextMenuStrip = menu;
                c.MouseEnter += (s, e) => Hovered = this;
                c.MouseLeave += (s, e) => { if (Hovered == this && !ClientRectangle.Contains(PointToClient(Cursor.Position))) Hovered = null; };
                U.Tip(c, "Drop a file here, click to browse, or copy the file in Explorer and press Ctrl+V here");
                c.AllowDrop = true;
                c.DragEnter += (s, e) => { e.Effect = e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None; };
                c.DragDrop += (s, e) => { var fs = e.Data.GetData(DataFormats.FileDrop) as string[]; if (fs != null && fs.Length > 0) Set(fs[0]); };
            }
            Layout += (s, e) => other.Location = new Point(Width - other.Width - 10, Height - other.Height - 8);
            Clear();
        }

        public static DropBox Hovered;   // the box under the mouse: where Ctrl+V goes
        public static string LastFolder;  // every file picker opens where the last file came from

        public void Choose()
        {
            using (var d = U.FileDialog(new OpenFileDialog { Filter = filter, Title = title }))
                if (d.ShowDialog(FindForm()) == DialogResult.OK) Set(d.FileName);
        }

        // The file (or folder) on the clipboard: copied in Explorer, or a path copied as text (quotes allowed).
        public static string ClipboardPath()
        {
            try
            {
                if (Clipboard.ContainsFileDropList())
                {
                    var l = Clipboard.GetFileDropList();
                    if (l.Count > 0) return l[0];
                }
                if (Clipboard.ContainsText())
                {
                    string t = Clipboard.GetText().Trim().Trim('"').Trim();
                    if (t.Length > 3 && t.Length < 400 && t.IndexOfAny(Path.GetInvalidPathChars()) < 0 && (System.IO.File.Exists(t) || Directory.Exists(t))) return t;
                }
            }
            catch (Exception) { }
            return null;
        }

        // Does this box take that file (by its file filter)? A folder is left to the page to judge.
        public bool Takes(string path) { return Directory.Exists(path) || FilterTakes(filter, path); }
        public static bool FilterTakes(string filter, string path)
        {
            string ext = Path.GetExtension(path).ToLowerInvariant();
            var parts = (filter ?? "").Split('|');
            for (int i = 1; i < parts.Length; i += 2)
                foreach (var pat in parts[i].Split(';'))
                    if (pat.Trim() == "*.*" || pat.Trim().ToLowerInvariant() == "*" + ext) return true;
            return false;
        }

        public bool Paste()
        {
            string p = ClipboardPath();
            if (p == null || !Takes(p)) { System.Media.SystemSounds.Beep.Play(); return false; }
            Set(p);
            return true;
        }

        public void Set(string path)
        {
            try { LastFolder = Directory.Exists(path) ? Path.GetDirectoryName(path.TrimEnd('\\')) : Path.GetDirectoryName(path); } catch (Exception) { }
            File = path;
            Filled("✓ " + Path.GetFileName(path), hint);
            if (Dropped != null) Dropped(path);
        }

        public void Filled(string text, string sub)
        {
            filledText = text;
            BackColor = Theme.OkBg; lt.TextAlign = ContentAlignment.MiddleLeft; lh.TextAlign = ContentAlignment.TopLeft;
            lt.Padding = new Padding(10, 0, 0, 0); lh.Padding = new Padding(10, 0, 0, 0);
            lt.Text = text; lh.Text = sub; lt.ForeColor = Theme.Ok; other.Visible = true; Cursor = Cursors.Default;
            Invalidate();
        }

        public void Clear()
        {
            File = null;
            BackColor = Theme.DropBg; lt.TextAlign = ContentAlignment.MiddleCenter; lh.TextAlign = ContentAlignment.TopCenter;
            lt.Padding = Padding.Empty; lh.Padding = Padding.Empty;
            lt.Text = title; lh.Text = hint; lt.ForeColor = Theme.Ink; other.Visible = false; Cursor = Cursors.Hand;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            using (var pen = new Pen(File == null ? Theme.DropLine : Theme.Ok, 2))
            {
                if (File == null) pen.DashStyle = DashStyle.Dash;
                e.Graphics.DrawRectangle(pen, 1, 1, Width - 3, Height - 3);
            }
        }
    }
}
