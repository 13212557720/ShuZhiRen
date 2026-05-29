import re
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path

BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / "env"


def load_env(path):
    config = {}
    if not path.exists():
        return config
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
            if m:
                config[m.group(1)] = m.group(2).strip()
    return config


class HighlightClipperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("游戏高光自动剪辑工具")
        self.root.geometry("800x650")
        self.root.minsize(600, 500)

        self.running = False
        self.thread = None
        self.log_queue = queue.Queue()

        self._build_ui()
        self._load_config()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_log()

    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main_frame, text="YouTube 视频链接 (一行一个):", font=("", 10, "bold")).pack(anchor=tk.W)

        self.url_text = scrolledtext.ScrolledText(main_frame, height=6, font=("Consolas", 10))
        self.url_text.pack(fill=tk.X, pady=(5, 10))

        config_frame = ttk.LabelFrame(main_frame, text="配置", padding=5)
        config_frame.pack(fill=tk.X, pady=(0, 10))

        row1 = ttk.Frame(config_frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="API Key:").pack(side=tk.LEFT)
        self.api_key_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.api_key_var, show="*", width=40).pack(side=tk.LEFT, padx=5)

        row2 = ttk.Frame(config_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="API地址:").pack(side=tk.LEFT)
        self.api_base_var = tk.StringVar(value="https://grsai.dakka.com.cn")
        ttk.Entry(row2, textvariable=self.api_base_var, width=40).pack(side=tk.LEFT, padx=5)

        row3 = ttk.Frame(config_frame)
        row3.pack(fill=tk.X, pady=2)
        ttk.Label(row3, text="模型:").pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value="gemini-3.1-pro")
        ttk.Entry(row3, textvariable=self.model_var, width=25).pack(side=tk.LEFT, padx=5)
        ttk.Label(row3, text="输出:").pack(side=tk.LEFT, padx=(20, 5))
        self.output_var = tk.StringVar(value=str(BASE_DIR / "output"))
        ttk.Entry(row3, textvariable=self.output_var, width=25).pack(side=tk.LEFT, padx=5)
        ttk.Button(row3, text="浏览", command=self._browse_output).pack(side=tk.LEFT)

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(5, 10))

        self.start_btn = ttk.Button(btn_frame, text="开始分析", command=self._start, width=15)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self._stop, state=tk.DISABLED, width=10)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.progress_var = tk.StringVar(value="等待开始...")
        ttk.Label(btn_frame, textvariable=self.progress_var).pack(side=tk.LEFT, padx=20)

        log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _load_config(self):
        config = load_env(ENV_PATH)
        key = config.get("GRSAI_OPENAI_API_KEY", "")
        base = config.get("GRSAI_OPENAI_API_BASE", "https://grsai.dakka.com.cn")
        if key:
            self.api_key_var.set(key)
        self.api_base_var.set(base)

    def _browse_output(self):
        d = filedialog.askdirectory(initialdir=self.output_var.get())
        if d:
            self.output_var.set(d)

    def _log(self, msg):
        self.log_queue.put(msg)

    def _poll_log(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
            except queue.Empty:
                break
        self.root.after(200, self._poll_log)

    def _start(self):
        urls_text = self.url_text.get("1.0", tk.END).strip()
        urls = [u.strip() for u in urls_text.split("\n") if u.strip()]

        if not urls:
            messagebox.showwarning("提示", "请输入至少一个YouTube视频链接")
            return

        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning("提示", "请输入API Key")
            return

        self.running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self._log(f"开始处理 {len(urls)} 个视频")

        self.thread = threading.Thread(target=self._run_pipeline, args=(urls,), daemon=True)
        self.thread.start()

    def _stop(self):
        self.running = False
        self._log("用户请求停止...")
        self.stop_btn.config(state=tk.DISABLED)

    def _run_pipeline(self, urls):
        from pipeline import Pipeline

        api_key = self.api_key_var.get().strip()
        base_url = self.api_base_var.get().strip()
        model = self.model_var.get().strip()
        output_dir = self.output_var.get().strip()

        pipeline = Pipeline(api_key, base_url, model, output_dir,
                            log_callback=self._log,
                            stop_check=lambda: not self.running)

        try:
            results = pipeline.run(urls)
        except Exception as e:
            self._log(f"严重错误: {e}")

        self.running = False
        self.root.after(0, self._on_done, results)

    def _on_done(self, results=None):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.progress_var.set("处理完成")

        if results:
            total_clips = sum(len(r.get("clips", [])) for r in results)
            errors = [r for r in results if r.get("error")]
            self._log(f"\n{'='*50}")
            self._log(f"全部完成! 共生成 {total_clips} 个高光片段")
            if errors:
                self._log(f"其中 {len(errors)} 个视频处理失败")
            for r in results:
                title = r.get("title", r.get("url", "?"))
                err = r.get("error")
                clips = r.get("clips", [])
                if err:
                    self._log(f"  ❌ {title}: {err}")
                else:
                    self._log(f"  ✅ {title}: {len(clips)} 个片段")
                    for c in clips:
                        self._log(f"     [{c['start']:.0f}s-{c['end']:.0f}s] {c['desc']}")

    def _on_close(self):
        if self.running:
            if messagebox.askyesno("确认", "任务正在运行中，确定要退出吗?"):
                self.running = False
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    app = HighlightClipperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
