#!/usr/bin/env python#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import os, time
from urllib.parse import urljoin, urlparse

BASE_URL = "https://drivedepobre.com/exemplo"
OUT_DIR = "downloads"
os.makedirs(OUT_DIR, exist_ok=True)

visited_folders = set()

def same_domain(url):
    return urlparse(url).netloc == urlparse(BASE_URL).netloc

def normalize(url):
    return urljoin(BASE_URL, url)

def process_folder(page, folder_url):
    """Processa uma pasta: retorna subpastas e links de arquivo"""
    print(f"Abrindo pasta: {folder_url}")
    page.goto(folder_url, wait_until="domcontentloaded", timeout=0)
    time.sleep(2)  # espera scripts carregarem

    anchors = page.query_selector_all("a[href]")
    subfolders = []
    file_links = []

    for a in anchors:
        href = a.get_attribute("href")
        if not href:
            continue
        full = normalize(href)
        if not same_domain(full):
            continue
        path = urlparse(full).path
        if path.startswith("/pasta/") and full not in visited_folders:
            subfolders.append(full)
        elif path.startswith("/arquivo/") or path.startswith("/pdf/") or path.endswith(".html"):
            file_links.append(full)

    return subfolders, file_links

def download_file(context, file_url):
    """Baixa o arquivo e salva preservando estrutura de pastas"""
    page = context.new_page()
    try:
        parsed = urlparse(file_url)
        # Cria caminho local equivalente à estrutura do site
        relative_path = parsed.path.lstrip("/").replace("/", os.sep)
        save_path = os.path.join(OUT_DIR, relative_path)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        print(f"Baixando: {file_url} -> {save_path}")

        # Espera o browser iniciar o download real
        with page.expect_download(timeout=120000) as download_info:  # 2 min timeout
            page.goto(file_url, wait_until="domcontentloaded", timeout=0)
            time.sleep(1)
            # Tenta clicar no botão ou link de download
            if page.locator("text=Download").count() > 0:
                page.locator("text=Download").first.click()
            elif page.locator("a[download]").count() > 0:
                page.locator("a[download]").first.click()
            else:
                print("Não achou botão de download, pulando...")
                page.close()
                return

        download = download_info.value
        download.save_as(save_path)
        print(f"Arquivo salvo: {save_path}")

    except Exception as e:
        print(f"Erro ao baixar {file_url}: {e}")
    finally:
        page.close()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=500)
    context = browser.new_context(
        accept_downloads=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )
    page = context.new_page()

    folders_to_visit = [BASE_URL]

    while folders_to_visit:
        folder = folders_to_visit.pop(0)
        if folder in visited_folders:
            continue
        visited_folders.add(folder)

        subfolders, files = process_folder(page, folder)
        folders_to_visit.extend(subfolders)

        for f in files:
            download_file(context, f)

    browser.close()
    print("Todos os arquivos foram processados!")

