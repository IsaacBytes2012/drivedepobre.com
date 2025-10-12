#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import os, time, re, random
from urllib.parse import urljoin

# 🧹 Limpa terminal
def clear_terminal():
    os.system("cls" if os.name == "nt" else "clear")
clear_terminal()

# 📥 Pergunta link raiz
BASE_URL = input("📥 Digite o link da pasta raiz que deseja baixar: ").strip()

# 📂 Pasta de destino
OUT_DIR = "downloads"
os.makedirs(OUT_DIR, exist_ok=True)
visited_folders = set()

# 🧼 Sanitiza nomes de arquivos/pastas
def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

def clean_file_name(text):
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

def clean_folder_name(name):
    name = re.sub(r'[^\w\s\-\.]', '', name)
    unwanted_prefixes = ["folder", "pasta", "dir", "subfolder"]
    for prefix in unwanted_prefixes:
        if name.lower().startswith(prefix.lower()):
            name = name[len(prefix):]
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def normalize(url):
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin("https://drivedepobre.com", url)

# 🌀 Scroll inteligente
def scroll_to_bottom(page, max_wait=5, step_delay=0.8, max_scrolls=1000):
    print("🌀 Iniciando scroll completo da página...")
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
                print(f"✅ Scroll finalizado após {scroll_count} roladas.")
                break
        else:
            stable_time = 0
            last_height = new_height
        scroll_count += 1
    time.sleep(2)

# 📁 Processa pasta
def process_folder(page, folder_url):
    print(f"\n📂 Acessando pasta: {folder_url}")
    try:
        page.goto(folder_url, wait_until="domcontentloaded", timeout=90000)
    except Exception as e:
        print(f"⚠️ Erro ao acessar {folder_url}: {e}")
        return [], []

    time.sleep(3)
    scroll_to_bottom(page)

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

    print(f"📑 Links encontrados: {len(file_links)} arquivos, {len(subfolders)} pastas.")
    return subfolders, file_links

# 📥 Download com detecção de erro na interface
def download_file(context, file_url, visible_name, local_path, max_attempts=4):
    os.makedirs(local_path, exist_ok=True)
    attempt = 1

    while attempt <= max_attempts:
        page = context.new_page()
        try:
            print(f"📥 Baixando ({attempt}/{max_attempts}): {visible_name}")
            page.goto(file_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(random.randint(5, 10))

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
            with page.expect_download(timeout=120000) as download_info:
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

            # 💾 Salva arquivo com nome limpo
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
            print(f"✅ Arquivo salvo: {save_path}")
            time.sleep(random.randint(2, 5))
            page.close()
            return

        except Exception as e:
            print(f"❌ Erro no download ({attempt}/{max_attempts}): {e}")
            attempt += 1
            page.close()
            if attempt <= max_attempts:
                retry_delay = random.randint(5, 15)
                print(f"🔄 Tentando novamente em {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                print(f"⚠️ Arquivo {visible_name} pulado após {max_attempts} tentativas.")

# 🚀 Execução principal
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, slow_mo=500)
    context = browser.new_context(
        accept_downloads=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, como Gecko) Chrome/120 Safari/537.36"
    )
    page = context.new_page()

    folders_to_visit = [(BASE_URL, "", OUT_DIR)]

    while folders_to_visit:
        folder_url, folder_name, local_path = folders_to_visit.pop(0)
        if folder_url in visited_folders:
            continue
        visited_folders.add(folder_url)

        subfolders, files = process_folder(page, folder_url)

        time.sleep(random.randint(3, 7))

        for sub_url, sub_name in subfolders:
            sub_name_clean = clean_folder_name(sub_name)
            sub_local_path = os.path.join(local_path, sub_name_clean)
            if sub_url not in visited_folders:
                folders_to_visit.append((sub_url, sub_name_clean, sub_local_path))

        for file_url, visible_name in files:
            download_file(context, file_url, visible_name, local_path)

    browser.close()
    print("\n✅ Todos os arquivos e pastas foram processados com scroll, retry e detecção de erro na interface!")