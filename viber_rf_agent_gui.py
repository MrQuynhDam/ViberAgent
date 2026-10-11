import glob
import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from google import genai
import requests

CONFIG_FILE = "viber_agent_config.json"

DEFAULT_PROMPT = """Bạn là một trợ lý AI chuyên về Tối ưu & Vận hành Mạng Vô tuyến (RF Engineer Assistant).
Dưới đây là toàn bộ tin nhắn trong khoảng thời gian vừa qua từ các nhóm Viber công việc khu vực Đồng Tháp (mã trạm DTP, TGG):

{CHAT_LOGS}

Hãy phân tích và tóm tắt lại thông tin theo cấu trúc chuyên môn sau:

🚨 *1. SỰ CỐ & CẢNH BÁO KHẨN* (Mất sóng/Cell-out, PIM, VSWR, Nhiễu, Mất điện/Truyền dẫn):
- Ghi rõ Mã trạm (*DTP, *TGG) -> Hiện trạng -> Nhân sự đang xử lý.

⚙️ *2. LỊCH THI CÔNG & TỐI ƯU HẠ TẦNG* (Tilt, Azimuth, Swap antenna, Cấu hình):
- Ghi rõ Mã trạm -> Nội dung tác động/thay đổi.

📊 *3. CHẤT LƯỢNG MẠNG & KPI* (Rớt cuộc gọi, Tốc độ chậm, Tải cao, Phản ánh KH):
- Mã trạm/Khu vực -> Vấn đề chi tiết.

📌 *4. ACTION ITEMS (VIỆC CẦN LÀM)*:
- [Mã trạm] - [Việc cần làm] - [Người phụ trách]

Lưu ý: Giữ nguyên mã trạm và thông số kỹ thuật. Bỏ qua tin nhắn chào hỏi, giao tiếp cá nhân."""


class ViberRFAgentApp:

    def __init__(self, root):
        self.root = root
        self.root.title("Viber RF Summary Agent - Đồng Tháp (DTP / TGG)")
        self.root.geometry("850x700")
        self.root.minsize(780, 620)

        self.running = False
        self.worker_thread = None

        self.setup_ui()
        self.load_config()

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        # TAB 1: CẤU HÌNH HỆ THỐNG
        self.tab_config = ttk.Frame(notebook)
        notebook.add(self.tab_config, text="⚙️ Cấu hình API & Viber")

        # TAB 2: PROMPT & CHU KỲ
        self.tab_prompt = ttk.Frame(notebook)
        notebook.add(self.tab_prompt, text="📝 Prompt & Chu kỳ")

        # TAB 3: ĐIỀU KHIỂN & BÁO CÁO
        self.tab_run = ttk.Frame(notebook)
        notebook.add(self.tab_run, text="🚀 Điều khiển & Nhật ký")

        self.build_tab_config()
        self.build_tab_prompt()
        self.build_tab_run()

    # ---------------- TAB 1: CONFIG ----------------
    def build_tab_config(self):
        frame = ttk.LabelFrame(
            self.tab_config, text="Thông tin kết nối hệ thống", padding=15
        )
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 1. Viber DB Path
        ttk.Label(frame, text="Đường dẫn file viber.db:").grid(
            row=0, column=0, sticky="w", pady=5
        )

        db_btn_frame = ttk.Frame(frame)
        db_btn_frame.grid(row=0, column=1, sticky="ew", padx=5, pady=5)

        self.entry_db_path = ttk.Entry(db_btn_frame, width=45)
        self.entry_db_path.pack(side="left", fill="x", expand=True, padx=(0, 5))

        ttk.Button(
            db_btn_frame, text="🔍 Dò tìm", command=self.auto_detect_viber_db
        ).pack(side="left", padx=2)
        ttk.Button(
            db_btn_frame, text="📁 Chọn file...", command=self.browse_db
        ).pack(side="left", padx=2)

        # 2. Gemini API Key
        ttk.Label(frame, text="Gemini API Key:").grid(
            row=1, column=0, sticky="w", pady=5
        )
        self.entry_gemini_key = ttk.Entry(frame, width=65, show="*")
        self.entry_gemini_key.grid(row=1, column=1, sticky="w", padx=5, pady=5)

        # 3. Telegram Token
        ttk.Label(frame, text="Telegram Bot Token:").grid(
            row=2, column=0, sticky="w", pady=5
        )
        self.entry_tele_token = ttk.Entry(frame, width=65, show="*")
        self.entry_tele_token.grid(row=2, column=1, sticky="w", padx=5, pady=5)

        # 4. Telegram Chat ID
        ttk.Label(frame, text="Telegram Chat ID:").grid(
            row=3, column=0, sticky="w", pady=5
        )
        self.entry_tele_chat_id = ttk.Entry(frame, width=65)
        self.entry_tele_chat_id.grid(
            row=3, column=1, sticky="w", padx=5, pady=5
        )

        # 5. Keywords lọc nhóm
        ttk.Label(
            frame, text="Từ khóa lọc nhóm Viber (phân cách bằng dấu phẩy):"
        ).grid(row=4, column=0, sticky="w", pady=5)
        self.entry_keywords = ttk.Entry(frame, width=65)
        self.entry_keywords.grid(row=4, column=1, sticky="w", padx=5, pady=5)

        # Button Save
        ttk.Button(
            frame, text="💾 Lưu cấu hình", command=self.save_config
        ).grid(row=5, column=1, sticky="e", pady=20)

    def auto_detect_viber_db(self):
        """Tự động tìm kiếm file viber.db trong các thư mục mặc định."""
        possible_patterns = [
            os.path.expanduser(r"~\AppData\Roaming\ViberPC\*\viber.db"),
            os.path.expanduser(
                r"~/Library/Application Support/ViberPC/*/viber.db"
            ),
        ]
        found = False
        for pattern in possible_patterns:
            matches = glob.glob(pattern)
            if matches:
                db_found = matches[0]
                self.entry_db_path.delete(0, tk.END)
                self.entry_db_path.insert(0, db_found)
                self.log(
                    f"🔍 Tự động phát hiện thành công viber.db tại: {db_found}"
                )
                found = True
                break

        if not found:
            messagebox.showwarning(
                "Thông báo",
                "Không tự động tìm thấy viber.db. Vui lòng bấm 'Chọn file...' để chọn thủ công.",
            )

    def browse_db(self):
        filename = filedialog.askopenfilename(
            title="Chọn file viber.db", filetypes=[("Database files", "*.db")]
        )
        if filename:
            self.entry_db_path.delete(0, tk.END)
            self.entry_db_path.insert(0, filename)

    # ---------------- TAB 2: PROMPT & CHU KỲ ----------------
    def build_tab_prompt(self):
        frame = ttk.Frame(self.tab_prompt, padding=15)
        frame.pack(fill="both", expand=True)

        # Chu kỳ chạy
        time_frame = ttk.Frame(frame)
        time_frame.pack(fill="x", pady=5)
        ttk.Label(
            time_frame, text="Khoảng thời gian mỗi lần tóm tắt (phút):"
        ).pack(side="left")
        self.spin_interval = ttk.Spinbox(
            time_frame, from_=5, to=1440, width=8, increment=5
        )
        self.spin_interval.pack(side="left", padx=10)
        self.spin_interval.set(60)

        # Prompt Box
        ttk.Label(
            frame,
            text="Cấu hình Prompt AI (Bắt buộc giữ nguyên từ khóa {CHAT_LOGS}):",
        ).pack(anchor="w", pady=(10, 5))
        self.txt_prompt = ScrolledText(frame, height=18, wrap="word")
        self.txt_prompt.pack(fill="both", expand=True, pady=5)
        self.txt_prompt.insert(tk.END, DEFAULT_PROMPT)

        ttk.Button(
            frame, text="💾 Lưu Prompt & Chu kỳ", command=self.save_config
        ).pack(anchor="e", pady=10)

    # ---------------- TAB 3: CONTROL & LOGS ----------------
    def build_tab_run(self):
        frame = ttk.Frame(self.tab_run, padding=15)
        frame.pack(fill="both", expand=True)

        # Control Panel
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=5)

        self.btn_start = ttk.Button(
            btn_frame, text="▶ BẮT ĐẦU CHẠY TỰ ĐỘNG", command=self.start_agent
        )
        self.btn_start.pack(side="left", padx=5)

        self.btn_stop = ttk.Button(
            btn_frame,
            text="⏹ DỪNG",
            command=self.stop_agent,
            state="disabled",
        )
        self.btn_stop.pack(side="left", padx=5)

        self.btn_run_now = ttk.Button(
            btn_frame, text="⚡ TÓM TẮT NGAY BÂY GIỜ", command=self.run_once_async
        )
        self.btn_run_now.pack(side="left", padx=5)

        self.lbl_status = ttk.Label(
            btn_frame, text="Trạng thái: Đang dừng", font=("Arial", 10, "bold")
        )
        self.lbl_status.pack(side="right", padx=10)

        # Log Window
        ttk.Label(frame, text="Nhật ký hoạt động (Live Logs):").pack(
            anchor="w", pady=(10, 5)
        )
        self.txt_log = ScrolledText(
            frame, height=20, bg="#1e1e1e", fg="#00ff00", font=("Consolas", 9)
        )
        self.txt_log.pack(fill="both", expand=True)

    def log(self, message):
        now = datetime.now().strftime("%H:%M:%S")
        self.txt_log.insert(tk.END, f"[{now}] {message}\n")
        self.txt_log.see(tk.END)

    # ---------------- LƯU & TẢI CẤU HÌNH ----------------
    def save_config(self):
        config = {
            "viber_db_path": self.entry_db_path.get(),
            "gemini_key": self.entry_gemini_key.get(),
            "tele_token": self.entry_tele_token.get(),
            "tele_chat_id": self.entry_tele_chat_id.get(),
            "keywords": self.entry_keywords.get(),
            "interval_minutes": self.spin_interval.get(),
            "prompt": self.txt_prompt.get("1.0", tk.END).strip(),
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            messagebox.showinfo(
                "Thành công", "Đã lưu cấu hình vào viber_agent_config.json!"
            )
            self.log("Đã cập nhật file cấu hình.")
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể lưu cấu hình: {e}")

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            # Điền mặc định nếu chưa có file cấu hình
            self.auto_detect_viber_db()
            self.entry_keywords.insert(0, "DTP, TGG, RF, Đồng Tháp")
            return

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                self.entry_db_path.delete(0, tk.END)
                self.entry_db_path.insert(
                    0, config.get("viber_db_path", "")
                )

                self.entry_gemini_key.delete(0, tk.END)
                self.entry_gemini_key.insert(0, config.get("gemini_key", ""))

                self.entry_tele_token.delete(0, tk.END)
                self.entry_tele_token.insert(0, config.get("tele_token", ""))

                self.entry_tele_chat_id.delete(0, tk.END)
                self.entry_tele_chat_id.insert(
                    0, config.get("tele_chat_id", "")
                )

                self.entry_keywords.delete(0, tk.END)
                self.entry_keywords.insert(0, config.get("keywords", ""))

                self.spin_interval.set(config.get("interval_minutes", "60"))

                if config.get("prompt"):
                    self.txt_prompt.delete("1.0", tk.END)
                    self.txt_prompt.insert(tk.END, config.get("prompt"))

            self.log("Đã nạp xong cấu hình từ viber_agent_config.json.")
        except Exception as e:
            self.log(f"Lỗi khi nạp cấu hình: {e}")

    # ---------------- LOGIC XỬ LÝ CHÍNH ----------------
    def run_process(self):
        db_path = self.entry_db_path.get()
        gemini_key = self.entry_gemini_key.get()
        tele_token = self.entry_tele_token.get()
        tele_chat_id = self.entry_tele_chat_id.get()
        keywords_str = self.entry_keywords.get()
        interval_min = int(self.spin_interval.get())
        prompt_template = self.txt_prompt.get("1.0", tk.END).strip()

        try:
            if not os.path.exists(db_path):
                self.log(f"❌ LỖI: Không tìm thấy viber.db tại {db_path}")
                return

            # Chuẩn hóa đường dẫn URI cho Windows (Bắt buộc phải có file:/// 3 dấu gạch)
            abs_db_path = os.path.abspath(db_path).replace("\\", "/")
            if not abs_db_path.startswith("/"):
                abs_db_path = "/" + abs_db_path
                
            # Thêm immutable=1 để ép SQLite đọc dữ liệu tĩnh, không đụng vào lock của Viber
            db_uri = f"file:{abs_db_path}?mode=ro&immutable=1"

            keywords = [
                k.strip().lower() for k in keywords_str.split(",") if k.strip()
            ]
            one_hour_ago_ms = int((time.time() - (interval_min * 60)) * 1000)

            # Kết nối trực tiếp URI
            conn = sqlite3.connect(db_uri, uri=True)
            cursor = conn.cursor()

            query = """
                SELECT 
                    COALESCE(ChatInfo.Name, 'Trò chuyện riêng') AS GroupName,
                    COALESCE(Contact.Name, Event.SenderID) AS SenderName,
                    Event.TimeStamp,
                    Event.Body
                FROM Event
                LEFT JOIN ChatInfo ON Event.ChatID = ChatInfo.ChatID
                LEFT JOIN Contact ON Event.ContactID = Contact.ContactID
                WHERE Event.TimeStamp >= ? 
                  AND Event.Body IS NOT NULL 
                  AND Event.Body != ''
                ORDER BY GroupName, Event.TimeStamp ASC
            """
            cursor.execute(query, (one_hour_ago_ms,))
            rows = cursor.fetchall()
            conn.close()

            # Lọc theo từ khóa nhóm
            grouped_messages = {}
            for group_name, sender_name, timestamp_ms, body in rows:
                if keywords:
                    if not any(kw in group_name.lower() for kw in keywords):
                        continue

                time_str = datetime.fromtimestamp(
                    timestamp_ms / 1000
                ).strftime("%H:%M")
                msg_fmt = f"[{time_str}] {sender_name}: {body}"

                if group_name not in grouped_messages:
                    grouped_messages[group_name] = []
                grouped_messages[group_name].append(msg_fmt)

            if not grouped_messages:
                self.log(
                    f"ℹ️ Không có tin nhắn mới nào trong {interval_min} phút qua thuộc các nhóm đã lọc."
                )
                return

            # Đóng gói dữ liệu tin nhắn
            chat_logs = ""
            for g_name, msgs in grouped_messages.items():
                chat_logs += f"=== NHÓM: {g_name} ===\n"
                chat_logs += "\n".join(msgs) + "\n\n"

            # Gọi Gemini API
            self.log("🤖 Đang gửi dữ liệu sang Gemini AI tóm tắt...")
            prompt = prompt_template.replace("{CHAT_LOGS}", chat_logs)

            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash", contents=prompt
            )

            summary_text = f"📡 *BÁO CÁO RF ĐỒNG THÁP ({interval_min} PHÚT VỪA QUA)*\n\n{response.text}"

            # Gửi báo cáo về Telegram Bot
            self.log("📲 Đang gửi kết quả về Telegram...")
            self.send_telegram(tele_token, tele_chat_id, summary_text)
            self.log("✅ HOÀN THÀNH CHU KỲ TÓM TẮT VÀ GỬI THÀNH CÔNG!")

        except Exception as e:
            self.log(f"❌ LỖI TRONG QUÁ TRÌNH XỬ LÝ: {e}")

    def send_telegram(self, token, chat_id, text):
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        max_length = 4000
        for i in range(0, len(text), max_length):
            chunk = text[i : i + max_length]
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
            }
            try:
                res = requests.post(url, json=payload, timeout=10)
                if res.status_code != 200:
                    payload.pop("parse_mode")
                    requests.post(url, json=payload, timeout=10)
            except Exception as e:
                self.log(f"⚠️ Lỗi kết nối Telegram: {e}")

    # ---------------- LUỒNG CHẠY TỰ ĐỘNG ----------------
    def start_agent(self):
        self.running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.lbl_status.config(text="Trạng thái: ĐANG CHẠY TỰ ĐỘNG", foreground="green")

        self.worker_thread = threading.Thread(
            target=self.auto_run_loop, daemon=True
        )
        self.worker_thread.start()

    def stop_agent(self):
        self.running = False
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_status.config(text="Trạng thái: Đang dừng", foreground="red")
        self.log("🛑 Đã dừng luồng tự động.")

    def auto_run_loop(self):
        interval_min = int(self.spin_interval.get())
        self.log(
            f"🚀 Kích hoạt Agent tự động (Chu kỳ: {interval_min} phút/lần)."
        )

        while self.running:
            self.log("--- Bắt đầu chu kỳ quét mới ---")
            self.run_process()

            interval_sec = int(self.spin_interval.get()) * 60
            for _ in range(interval_sec):
                if not self.running:
                    break
                time.sleep(1)

    def run_once_async(self):
        threading.Thread(target=self.run_process, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    app = ViberRFAgentApp(root)
    root.mainloop()
