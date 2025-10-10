#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import os, time, re
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

# Sanitiza nomes de arquivos/pastas (remove apenas caracteres inválidos)
def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

# Limpa nomes de arquivos e remove prefixos indesejados
def clean_file_name(text):
    text = re.sub(r'[^\w\s\-\.]', '', text, flags=re.UNICODE)

    # 🔹 Remove prefixos indesejados no começo
    unwanted_prefixes = [
        "picture_as_pdf", "Resolva_", "arquivo_", "download_", "video_"
    ]
    for prefix in unwanted_prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):]

    # 🔹 Remove palavras desnecessárias no meio
    remove_words = [
        "picture", "pdf", "arquivo", "baixar", "download",
        "documento", "file", "imagem", "video", "mp4"
    ]
    pattern = r'\b(?:' + '|'.join(remove_words) + r')\b'
    text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    # 🔹 Remove espaços extras
    text = re.sub(r'\s+', ' ', text).strip()

    # 🔹 Remove extensão no final
    text = re.sub(r'\.(pdf|mp4|jpg|png|docx|xlsx|zip|rar)$', '', text, flags=re.IGNORECASE)

    return text

# Limpa nomes de pastas e remove prefixos indesejados
def clean_folder_name(name):
    name = re.sub(r'[^\w\s\-\.]', '', name)  # remove caracteres inválidos
    # 🔹 Remove prefixos comuns em pastas
    unwanted_prefixes = ["folder", "pasta", "dir", "subfolder"]
    for prefix in unwanted_prefixes:
        if name.lower().startswith(prefix.lower()):
            name = name[len(prefix):]
    # 🔹 Remove espaços extras
    name = re.sub(r'\s+', ' ', name).strip()
    return name

# Normaliza URLs relativas
def normalize(url):
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return urljoin("https://drivedepobre.com", url)

# Processa uma pasta: retorna subpastas e arquivos
def process_folder(page, folder_url):
    print(f"📂 Acessando pasta: {folder_url}")
    try:
        # ⏳ Timeout maior e espera completa do DOM
        page.goto(folder_url, wait_until="domcontentloaded", timeout=90000)  # 90 segundos
    except Exception as e:
        print(f"⚠️ Erro ao acessar {folder_url}: {e}")
        return [], []

    # 🔹 Espera extra para carregamento completo do conteúdo dinâmico (15 segundos)
    time.sleep(15)

    # 🔹 Subpastas
    subfolder_anchors = page.locator("a.text-dark.fw-medium[href^='/pasta/']").all()
    subfolders = []
    for a in subfolder_anchors:
        href = a.get_attribute("href")
        full_url = normalize(href)
        visible_name = a.evaluate("el => el.childNodes[el.childNodes.length-1].textContent.trim()")
        subfolders.append((full_url, visible_name))

    # 🔹 Arquivos
    file_anchors = page.locator("a[href^='/arquivo/'], a[href^='/pdf/'], a[href$='.html']").all()
    file_links = []
    for a in file_anchors:
        href = a.get_attribute("href")
        full_url = normalize(href)
        visible_name = a.evaluate("el => el.childNodes[el.childNodes.length-1].textContent.trim()")
        file_links.append((full_url, visible_name))

    return subfolders, file_links

# Função de download
def download_file(context, file_url, visible_name, local_path):
    page = context.new_page()
    try:
        os.makedirs(local_path, exist_ok=True)

        with page.expect_download(timeout=120000) as download_info:
            page.goto(file_url, wait_until="domcontentloaded", timeout=0)
            time.sleep(1)

            if page.locator("text=Download").count() > 0:
                page.locator("text=Download").first.click()
            elif page.locator("a[download]").count() > 0:
                page.locator("a[download]").first.click()
            else:
                print(f"⚠️ Nenhum botão de download encontrado em {file_url}")
                page.close()
                return

        download = download_info.value
        suggested = download.suggested_filename
        ext = os.path.splitext(suggested)[1] if suggested else ""

        # 🔹 Mantém nome exato para vídeos .mp4
        if visible_name.lower().endswith(".mp4"):
            final_name = sanitize_filename(visible_name)
        else:
            final_name = clean_file_name(visible_name).rstrip(".") + ext
            final_name = sanitize_filename(final_name)

        save_path = os.path.join(local_path, final_name)

        # Evita sobrescrever arquivos
        base, ext = os.path.splitext(save_path)
        counter = 1
        while os.path.exists(save_path):
            save_path = f"{base} ({counter}){ext}"
            counter += 1

        download.save_as(save_path)
        print(f"✅ Arquivo salvo: {save_path}")

    except Exception as e:
        print(f"❌ Erro ao baixar {file_url}: {e}")
    finally:
        page.close()

# 🔹 Execução principal
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=500)
    context = browser.new_context(
        accept_downloads=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, como Gecko) Chrome/120 Safari/537.36"
    )
    page = context.new_page()

    # Inicializa fila com a pasta raiz
    folders_to_visit = [(BASE_URL, "", OUT_DIR)]  # Tudo começa em "downloads"

    while folders_to_visit:
        folder_url, folder_name, local_path = folders_to_visit.pop(0)
        if folder_url in visited_folders:
            continue
        visited_folders.add(folder_url)

        subfolders, files = process_folder(page, folder_url)

        # Adiciona subpastas na fila com nomes limpos
        for sub_url, sub_name in subfolders:
            sub_name_clean = clean_folder_name(sub_name)
            sub_local_path = os.path.join(local_path, sub_name_clean)
            if sub_url not in visited_folders:
                folders_to_visit.append((sub_url, sub_name_clean, sub_local_path))

        # Baixa arquivos da pasta atual
        for file_url, visible_name in files:
            download_file(context, file_url, visible_name, local_path)

    browser.close()
    print("\n✅ Todos os arquivos e pastas foram baixados e limpos em 'downloads'!")
