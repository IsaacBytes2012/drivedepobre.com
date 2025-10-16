#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Downloader com GUI Tkinter baseado no script original.
Mantém a lógica original (scroll, descobrir subpastas/arquivos, retries, detecção de erro na interface),
adicionando interface para escolher pasta, ajustar tempos e ver logs em tempo real.
"""

import os
import re
import time
import random
import threading
from urllib.parse import urljoin
from queue import Queue, Empty

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from playwright.sync_api import sync_playwright

# -------------------------
# Funções utilitárias (mesma lógica do script original)
# -------------------------

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

def clean_file_name(text: str) -> str:
    text = re.sub(r'[^\w\s\-\.]', '', text, flags=re.UNICODE)
    unwanted_prefixes = ["picture_as_pdf", "Resolva_", "arquivo_", "download_", "video_"]
    for prefix in unwanted_prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):]
    remove_words = ["picture", "pdf", "arquivo", "baixar", "download", "documento", "file", "imagem", "video", "mp4"]
    pattern = r'\b(?:' + '|'.join(remove_words) + r')\b'
    text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'\.(pdf|mp4|jpg|png|docx|xlsx|zip|rar)$', '', text, flags=re.IGNORECASE)
    return text

def clean_folder_name(name: str) -> str:
    name = re.sub(r'[^\w\s\-\.]', '', name)
    unwanted_prefixes = ["folder", "pasta", "dir", "subfolder"]
    for prefix in unwanted_prefixes:
        if name.lower().startswith(prefix.lower()):
            name = name[len(prefix):]
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def normalize(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin("https://drivedepobre.com", url)

# -------------------------
# GUI-aware logging (thread-safe)
# -------------------------
class GuiLogger:
    def __init__(self, text_widget: ScrolledText, list_widget: ttk.Treeview):
        self.text_widget = text_widget
        self.list_widget = list_widget
        self.queue = Queue()
        # Start polling the queue to display messages in the GUI
    def log(self, message: str):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.queue.put(f"[{timestamp}] {message}")
    def poll(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                # append to scrolled text
                self.text_widget.configure(state="normal")
                self.text_widget.insert(tk.END, msg + "\n")
                self.text_widget.see(tk.END)
                self.text_widget.configure(state="disabled")
        except Empty:
            pass
        # schedule next poll by caller

    def set_item_status(self, item_id: str, status: str):
        # Update or insert an item into the treeview: (id, filename, status)
        # Expect item_id to be unique path
        try:
            if self.list_widget.exists(item_id):
                self.list_widget.set(item_id, "status", status)
            else:
                # insert with filename column and status column
                filename = os.path.basename(item_id)
                self.list_widget.insert("", "end", iid=item_id, values=(filename, status))
        except Exception:
            pass

# -------------------------
# Lógica de download e crawling (preservada)
# -------------------------

def scroll_to_bottom(page, max_wait=5, step_delay=0.8, max_scrolls=1000, logger: GuiLogger=None):
    if logger: logger.log("🌀 Iniciando scroll completo da página...")
    last_height = page.evaluate("() => document.body.scrollHeight")
    stable_time = 0
    scroll_count = 0
    while scroll_count < max_scrolls:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(step_delay)
        new_height = page.evaluate("() => document.body.scrollHeight")
        if new_height == last_height:
            stable_time += step_delay
            if stable_time >= max_wait:
                if logger: logger.log(f"✅ Scroll finalizado após {scroll_count} roladas.")
                break
        else:
            stable_time = 0
            last_height = new_height
        scroll_count += 1
    time.sleep(1)

def process_folder(page, folder_url, logger: GuiLogger=None):
    if logger: logger.log(f"📂 Acessando pasta: {folder_url}")
    try:
        page.goto(folder_url, wait_until="domcontentloaded", timeout=90000)
    except Exception as e:
        if logger: logger.log(f"⚠️ Erro ao acessar {folder_url}: {e}")
        return [], []

    time.sleep(1)
    # Scroll usando função original
    try:
        scroll_to_bottom(page, logger=logger)
    except Exception as e:
        if logger: logger.log(f"⚠️ Erro no scroll: {e}")

    # Seletores originais
    subfolder_anchors = page.locator("a.text-dark.fw-medium[href^='/pasta/']").all()
    subfolders = []
    for a in subfolder_anchors:
        href = a.get_attribute("href")
        if not href:
            continue
        full_url = normalize(href)
        try:
            visible_name = a.evaluate("el => el.childNodes[el.childNodes.length-1].textContent.trim()")
        except Exception:
            visible_name = "subfolder"
        subfolders.append((full_url, visible_name))

    file_anchors = page.locator("a[href^='/arquivo/'], a[href^='/pdf/'], a[href$='.html']").all()
    file_links = []
    for a in file_anchors:
        href = a.get_attribute("href")
        if not href:
            continue
        full_url = normalize(href)
        try:
            visible_name = a.evaluate("el => el.childNodes[el.childNodes.length-1].textContent.trim()")
        except Exception:
            visible_name = "file"
        file_links.append((full_url, visible_name))

    if logger: logger.log(f"📑 Links encontrados: {len(file_links)} arquivos, {len(subfolders)} pastas.")
    return subfolders, file_links

def download_file(context, file_url, visible_name, local_path,
                  logger: GuiLogger=None,
                  max_attempts=4,
                  pre_wait_random=(5,10),
                  retry_random_delay=(5,15),
                  expect_download_timeout=120000):
    os.makedirs(local_path, exist_ok=True)
    attempt = 1
    while attempt <= max_attempts:
        page = context.new_page()
        try:
            if logger: logger.log(f"📥 Baixando ({attempt}/{max_attempts}): {visible_name} -> {local_path}")
            logger and logger.set_item_status(os.path.join(local_path, visible_name), f"baixando ({attempt})")
            page.goto(file_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(random.randint(*pre_wait_random))

            btn_download = page.locator("text=Download")
            link_direct = page.locator("a[download]")

            # Aguarda botão/link aparecer
            found = False
            for _ in range(30):
                try:
                    if btn_download.count() > 0 or link_direct.count() > 0:
                        found = True
                        break
                except Exception:
                    pass
                time.sleep(1)

            if not found:
                raise Exception("Botão/link de download não encontrado.")

            # Clica e verifica erro visual
            with page.expect_download(timeout=expect_download_timeout) as download_info:
                if btn_download.count() > 0:
                    btn_download.first.click()
                else:
                    link_direct.first.click()

                # ⏳ Espera um pouco e verifica mensagem de erro
                time.sleep(5)
                error_elem = page.locator('text="Erro no download."')
                if error_elem.count() > 0:
                    raise Exception("Interface exibiu: Erro no download.")

            download = download_info.value

            # 💾 Salva arquivo com nome limpo (mesma lógica)
            suggested = download.suggested_filename
            ext = os.path.splitext(suggested)[1] if suggested else ""
            if visible_name.lower().endswith(".mp4"):
                final_name = sanitize_filename(visible_name)
            else:
                final_name = clean_file_name(visible_name).rstrip(".") + ext
                final_name = sanitize_filename(final_name)

            save_path = os.path.join(local_path, final_name)
            base, ext = os.path.splitext(save_path)
            counter = 1
            while os.path.exists(save_path):
                save_path = f"{base} ({counter}){ext}"
                counter += 1

            download.save_as(save_path)
            if logger: logger.log(f"✅ Arquivo salvo: {save_path}")
            logger and logger.set_item_status(save_path, "concluído")
            time.sleep(random.randint(2, 5))
            page.close()
            return

        except Exception as e:
            if logger: logger.log(f"❌ Erro no download ({attempt}/{max_attempts}): {e}")
            logger and logger.set_item_status(os.path.join(local_path, visible_name), f"erro ({attempt})")
            attempt += 1
            try:
                page.close()
            except Exception:
                pass
            if attempt <= max_attempts:
                retry_delay = random.randint(*retry_random_delay)
                if logger: logger.log(f"🔄 Tentando novamente em {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                if logger: logger.log(f"⚠️ Arquivo {visible_name} pulado após {max_attempts} tentativas.")
                logger and logger.set_item_status(os.path.join(local_path, visible_name), "pulado")

# -------------------------
# Thread de execução principal (para não travar a GUI)
# -------------------------

class DownloaderThread(threading.Thread):
    def __init__(self, base_url, out_dir, settings, logger: GuiLogger, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.base_url = base_url
        self.out_dir = out_dir
        self.settings = settings
        self.logger = logger
        self.stop_event = stop_event
        self.visited_folders = set()

    def run(self):
        # Inicia playwright e aplica configuracões
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, slow_mo=self.settings.get("slow_mo", 0))
            context = browser.new_context(
                accept_downloads=True,
                user_agent=self.settings.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            )
            page = context.new_page()

            folders_to_visit = [(self.base_url, "", self.out_dir)]
            while folders_to_visit and not self.stop_event.is_set():
                folder_url, folder_name, local_path = folders_to_visit.pop(0)
                if folder_url in self.visited_folders:
                    continue
                self.visited_folders.add(folder_url)

                subfolders, files = process_folder(page, folder_url, logger=self.logger)

                # pequena espera entre visitas
                time.sleep(random.randint(*self.settings.get("between_folder_wait", (3,7))))

                for sub_url, sub_name in subfolders:
                    if self.stop_event.is_set():
                        break
                    sub_name_clean = clean_folder_name(sub_name)
                    sub_local_path = os.path.join(local_path, sub_name_clean)
                    if sub_url not in self.visited_folders:
                        folders_to_visit.append((sub_url, sub_name_clean, sub_local_path))

                for file_url, visible_name in files:
                    if self.stop_event.is_set():
                        break
                    # registra no logger/lista
                    self.logger.set_item_status(os.path.join(local_path, visible_name), "na fila")
                    download_file(
                        context,
                        file_url,
                        visible_name,
                        local_path,
                        logger=self.logger,
                        max_attempts=self.settings.get("max_attempts", 4),
                        pre_wait_random=self.settings.get("pre_wait_random", (5,10)),
                        retry_random_delay=self.settings.get("retry_random_delay", (5,15)),
                        expect_download_timeout=self.settings.get("expect_download_timeout", 120000)
                    )

            try:
                browser.close()
            except Exception:
                pass
            self.logger.log("✅ Todos os arquivos/pastas processados (ou execução parada).")

# -------------------------
# GUI
# -------------------------

class App:
    def __init__(self, root):
        self.root = root
        root.title("Downloader Drivedepobre — GUI")
        root.geometry("900x640")
        style = ttk.Style(root)
        # tenta tema nativo, se disponível
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Frame de configurações
        frm_top = ttk.Frame(root, padding=10)
        frm_top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(frm_top, text="Link da pasta raiz:").grid(row=0, column=0, sticky=tk.W)
        self.entry_url = ttk.Entry(frm_top, width=70)
        self.entry_url.grid(row=0, column=1, columnspan=3, padx=6, sticky=tk.W)

        ttk.Label(frm_top, text="Pasta de destino:").grid(row=1, column=0, sticky=tk.W, pady=(6,0))
        self.out_dir_var = tk.StringVar(value=os.path.join(os.getcwd(), "downloads"))
        self.entry_outdir = ttk.Entry(frm_top, textvariable=self.out_dir_var, width=55)
        self.entry_outdir.grid(row=1, column=1, sticky=tk.W, pady=(6,0))
        ttk.Button(frm_top, text="Escolher...", command=self.choose_outdir).grid(row=1, column=2, sticky=tk.W, padx=(6,0), pady=(6,0))

        # Configurações de tempo e tentativas
        frm_opts = ttk.LabelFrame(root, text="Opções", padding=10)
        frm_opts.pack(side=tk.TOP, fill=tk.X, padx=10, pady=6)

        ttk.Label(frm_opts, text="Max tentativas por arquivo:").grid(row=0, column=0, sticky=tk.W)
        self.spin_max_attempts = ttk.Spinbox(frm_opts, from_=1, to=10, width=5)
        self.spin_max_attempts.set(4)
        self.spin_max_attempts.grid(row=0, column=1, sticky=tk.W, padx=(6,20))

        ttk.Label(frm_opts, text="Scroll max_wait (s):").grid(row=0, column=2, sticky=tk.W)
        self.entry_scroll_max_wait = ttk.Entry(frm_opts, width=6)
        self.entry_scroll_max_wait.insert(0, "5")
        self.entry_scroll_max_wait.grid(row=0, column=3, sticky=tk.W, padx=(6,20))

        ttk.Label(frm_opts, text="Scroll step_delay (s):").grid(row=0, column=4, sticky=tk.W)
        self.entry_step_delay = ttk.Entry(frm_opts, width=6)
        self.entry_step_delay.insert(0, "0.8")
        self.entry_step_delay.grid(row=0, column=5, sticky=tk.W, padx=(6,20))

        ttk.Label(frm_opts, text="Pre-wait random (s) min,max:").grid(row=1, column=0, sticky=tk.W, pady=(6,0))
        self.entry_prewait = ttk.Entry(frm_opts, width=10)
        self.entry_prewait.insert(0, "5,10")
        self.entry_prewait.grid(row=1, column=1, sticky=tk.W, padx=(6,20), pady=(6,0))

        ttk.Label(frm_opts, text="Retry delay random (s) min,max:").grid(row=1, column=2, sticky=tk.W, pady=(6,0))
        self.entry_retrywait = ttk.Entry(frm_opts, width=10)
        self.entry_retrywait.insert(0, "5,15")
        self.entry_retrywait.grid(row=1, column=3, sticky=tk.W, padx=(6,20), pady=(6,0))

        ttk.Label(frm_opts, text="Expect download timeout (ms):").grid(row=1, column=4, sticky=tk.W, pady=(6,0))
        self.entry_expect_timeout = ttk.Entry(frm_opts, width=10)
        self.entry_expect_timeout.insert(0, "120000")
        self.entry_expect_timeout.grid(row=1, column=5, sticky=tk.W, padx=(6,20), pady=(6,0))

        # Botões de controle
        frm_controls = ttk.Frame(root, padding=(10,0,10,0))
        frm_controls.pack(side=tk.TOP, fill=tk.X)
        self.btn_start = ttk.Button(frm_controls, text="Iniciar", command=self.start)
        self.btn_start.pack(side=tk.LEFT)
        self.btn_stop = ttk.Button(frm_controls, text="Parar", command=self.stop, state="disabled")
        self.btn_stop.pack(side=tk.LEFT, padx=(6,0))

        ttk.Button(frm_controls, text="Limpar log", command=self.clear_log).pack(side=tk.LEFT, padx=(6,0))
        ttk.Button(frm_controls, text="Salvar log...", command=self.save_log).pack(side=tk.LEFT, padx=(6,0))

        # Middle frame: list de downloads + log
        frm_middle = ttk.Frame(root, padding=10)
        frm_middle.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Treeview de status
        tree_columns = ("filename", "status")
        self.tree = ttk.Treeview(frm_middle, columns=tree_columns, show="headings", height=8)
        self.tree.heading("filename", text="Arquivo")
        self.tree.heading("status", text="Status")
        self.tree.column("filename", width=420)
        self.tree.column("status", width=140, anchor=tk.CENTER)
        self.tree.pack(side=tk.TOP, fill=tk.X)

        # Log scrolled text
        ttk.Label(frm_middle, text="Log:").pack(anchor=tk.W, pady=(8,0))
        self.txt_log = ScrolledText(frm_middle, height=16, state="disabled", wrap=tk.WORD)
        self.txt_log.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Status bar
        self.status_var = tk.StringVar(value="Pronto")
        self.lbl_status = ttk.Label(root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        self.lbl_status.pack(side=tk.BOTTOM, fill=tk.X)

        # Logger auxiliar
        self.logger = GuiLogger(self.txt_log, self.tree)

        # Thread control
        self.downloader_thread = None
        self.stop_event = threading.Event()

        # Poll logger queue
        self.root.after(200, self._poll_logger)

    def choose_outdir(self):
        d = filedialog.askdirectory(initialdir=self.out_dir_var.get() or os.getcwd())
        if d:
            self.out_dir_var.set(d)

    def clear_log(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.configure(state="disabled")

    def save_log(self):
        initial = os.path.join(self.out_dir_var.get(), "download_log.txt")
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=initial,
                                            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self.txt_log.get("1.0", tk.END))
                messagebox.showinfo("Salvar log", f"Log salvo em:\n{path}")
            except Exception as e:
                messagebox.showerror("Erro", f"Não foi possível salvar o log: {e}")

    def _poll_logger(self):
        self.logger.poll()
        # também atualiza status de botões
        if self.downloader_thread and self.downloader_thread.is_alive():
            self.status_var.set("Executando...")
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
        else:
            self.status_var.set("Pronto")
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled")
        self.root.after(200, self._poll_logger)

    def _gather_settings(self):
        # converte entradas para tipos corretos
        def parse_tuple(s, default=(5,10)):
            try:
                parts = [int(x.strip()) for x in s.split(",") if x.strip()]
                if len(parts) >= 2:
                    return (parts[0], parts[1])
            except Exception:
                pass
            return default
        settings = {}
        try:
            settings["max_attempts"] = int(self.spin_max_attempts.get())
        except Exception:
            settings["max_attempts"] = 4
        try:
            settings["scroll_max_wait"] = float(self.entry_scroll_max_wait.get())
        except Exception:
            settings["scroll_max_wait"] = 5.0
        try:
            settings["step_delay"] = float(self.entry_step_delay.get())
        except Exception:
            settings["step_delay"] = 0.8

        settings["pre_wait_random"] = parse_tuple(self.entry_prewait.get(), (5,10))
        settings["retry_random_delay"] = parse_tuple(self.entry_retrywait.get(), (5,15))
        try:
            settings["expect_download_timeout"] = int(self.entry_expect_timeout.get())
        except Exception:
            settings["expect_download_timeout"] = 120000

        # other small settings for playwright
        settings["slow_mo"] = 0  # mantemos 0 por padrão
        settings["user_agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, como Gecko) Chrome/120 Safari/537.36"

        # small mapping to pass proper args into scroll and download (keeps logic same)
        settings["scroll_args"] = {
            "max_wait": settings["scroll_max_wait"],
            "step_delay": settings["step_delay"]
        }
        # between folder wait
        settings["between_folder_wait"] = (3, 7)
        return settings

    def start(self):
        base_url = self.entry_url.get().strip()
        if not base_url:
            messagebox.showwarning("URL faltando", "Digite o link da pasta raiz para iniciar.")
            return
        out_dir = self.out_dir_var.get().strip() or os.path.join(os.getcwd(), "downloads")
        os.makedirs(out_dir, exist_ok=True)

        # define settings
        settings = self._gather_settings()
        # pass these specific waits into the worker loop via the settings mapping:
        # We'll adapt calls inside thread to use them where needed.

        # Clean tree and log
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.clear_log()

        self.stop_event.clear()
        self.downloader_thread = DownloaderThread(
            base_url=base_url,
            out_dir=out_dir,
            settings={
                "max_attempts": settings["max_attempts"],
                "pre_wait_random": settings["pre_wait_random"],
                "retry_random_delay": settings["retry_random_delay"],
                "expect_download_timeout": settings["expect_download_timeout"],
                "slow_mo": settings["slow_mo"],
                "user_agent": settings["user_agent"],
                "between_folder_wait": settings["between_folder_wait"]
            },
            logger=self.logger,
            stop_event=self.stop_event
        )
        # Monkey patch scroll parameters into process_folder by setting a small global? Instead, we override scroll function behavior:
        # We'll wrap page.evaluate usage by temporarily setting attributes in logger for scroll args. Simpler: modify scroll_to_bottom to consult settings if provided.
        # For now, scroll_to_bottom uses fixed signature; we will rely on default step_delay and max_wait in code; but to honor inputs, we will replace
        # the global scroll_to_bottom with a partial that uses user values. EASIEST approach: set attributes on the logger to be read by scroll_to_bottom.
        # But to avoid changing core logic, we will instead set environment variables-like global variables:
        global _GUI_SCROLL_MAX_WAIT, _GUI_STEP_DELAY
        _GUI_SCROLL_MAX_WAIT = settings["scroll_max_wait"]
        _GUI_STEP_DELAY = settings["step_delay"]

        self.logger.log(f"▶️ Iniciando download: {base_url}")
        self.downloader_thread.start()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")

    def stop(self):
        if messagebox.askyesno("Parar execução", "Deseja realmente parar a execução?"):
            self.stop_event.set()
            self.logger.log("⏹️ Parada solicitada pelo usuário. Aguardando término das operações em progresso...")
            self.btn_stop.config(state="disabled")
            # thread fechará quando verificar stop_event
        else:
            return

# Provide compatibility for scroll settings via globals used in scroll_to_bottom call
# We'll adapt scroll_to_bottom to read these globals if present.
try:
    _GUI_SCROLL_MAX_WAIT
except NameError:
    _GUI_SCROLL_MAX_WAIT = None
try:
    _GUI_STEP_DELAY
except NameError:
    _GUI_STEP_DELAY = None

# Patch the scroll_to_bottom function to consult globals if present (keeps logic same)
_original_scroll_to_bottom = scroll_to_bottom
def _scroll_to_bottom_patched(page, max_wait=5, step_delay=0.8, max_scrolls=1000, logger: GuiLogger=None):
    mm = max_wait
    ss = step_delay
    if _GUI_SCROLL_MAX_WAIT is not None:
        try:
            mm = float(_GUI_SCROLL_MAX_WAIT)
        except Exception:
            pass
    if _GUI_STEP_DELAY is not None:
        try:
            ss = float(_GUI_STEP_DELAY)
        except Exception:
            pass
    return _original_scroll_to_bottom(page, max_wait=mm, step_delay=ss, max_scrolls=max_scrolls, logger=logger)

# Replace reference in module
scroll_to_bottom = _scroll_to_bottom_patched

# Also allow process_folder to pass logger into scroll_to_bottom (already done)

# -------------------------
# Execução principal da GUI
# -------------------------
def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()

if __name__ == "__main__":
    main()

