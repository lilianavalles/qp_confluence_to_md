import os
import re
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CONFLUENCE_BASE_URL", "https://nhsd-confluence.digital.nhs.uk").rstrip("/")
TOKEN = os.getenv("CONFLUENCE_API_TOKEN")
SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY", "DAT")
OUTPUT_DIR = "./exported_md"
SEED_PAGE_ID = "465639889"

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

os.makedirs(OUTPUT_DIR, exist_ok=True)
downloaded_pages = {}

def sanitize_filename(title):
    return re.sub(r'[\\/*?:"<>|]', "-", title).strip()

def get_page_by_id(page_id):
    url = f"{BASE_URL}/rest/api/content/{page_id}?expand=body.storage"
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        return res.json()
    return None

def get_page_by_title(title):
    url = f"{BASE_URL}/rest/api/content?spaceKey={SPACE_KEY}&title={requests.utils.quote(title)}&expand=body.storage"
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        results = res.json().get("results", [])
        return results[0] if results else None
    return None

def preprocess_confluence_html(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    for ac_link in soup.find_all(["ac:link", "link"]):
        ri_page = ac_link.find(["ri:page", "page"])
        link_text_el = ac_link.find(["ac:plain-text-link-body", "ac:link-body", "plain-text-link-body", "link-body"])
        
        page_title = None
        if ri_page and ri_page.has_attr("ri:content-title"):
            page_title = ri_page["ri:content-title"]
        elif ri_page and ri_page.has_attr("content-title"):
            page_title = ri_page["content-title"]
            
        display_text = link_text_el.get_text(strip=True) if link_text_el else page_title
        
        if display_text and page_title:
            clean_link = soup.new_tag("a", href=f"{BASE_URL}/display/{SPACE_KEY}/{page_title.replace(' ', '+')}")
            clean_link.string = display_text
            ac_link.replace_with(clean_link)
        elif display_text:
            ac_link.replace_with(display_text)
            
    return str(soup)

def process_and_save_page(page_data):
    page_id = page_data["id"]
    title = page_data["title"]
    clean_title = sanitize_filename(title)
    file_name = f"{clean_title}.md"
    file_path = os.path.join(OUTPUT_DIR, file_name)

    html_body = page_data.get("body", {}).get("storage", {}).get("value", "")
    cleaned_html = preprocess_confluence_html(html_body)
    markdown_body = md(cleaned_html, heading_style="ATX", strip=['script', 'style'])

    md_content = f"# {title}\n\n{markdown_body}"
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    downloaded_pages[page_id] = file_name
    downloaded_pages[title] = file_name
    print(f"Saved: {file_path}")
    return file_path, markdown_body

def extract_referenced_titles(markdown_text):
    pattern = r"https?://nhsd-confluence\.digital\.nhs\.uk/display/DAT/([^)\s]+)"
    matches = re.findall(pattern, markdown_text)
    return [m.replace("+", " ") for m in matches]

def convert_external_links_to_local_md():
    print("\nLinking Markdown files together with local relative paths...")
    for file_name in os.listdir(OUTPUT_DIR):
        if not file_name.endswith(".md"):
            continue
        
        file_path = os.path.join(OUTPUT_DIR, file_name)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        def replace_link(match):
            anchor_text = match.group(1)
            raw_target = match.group(2).replace("+", " ")
            if raw_target in downloaded_pages:
                local_md = downloaded_pages[raw_target]
                return f"[{anchor_text}](./{requests.utils.quote(local_md)})"
            return match.group(0)

        pattern = r"\[([^\]]+)\]\(https?://nhsd-confluence\.digital\.nhs\.uk/display/DAT/([^)]+)\)"
        updated_content = re.sub(pattern, replace_link, content)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(updated_content)

# Execution
queue = [SEED_PAGE_ID]
processed_ids = set()

seed_data = get_page_by_id(SEED_PAGE_ID)
if seed_data:
    child_url = f"{BASE_URL}/rest/api/content/{SEED_PAGE_ID}/child/page"
    c_res = requests.get(child_url, headers=headers)
    if c_res.status_code == 200:
        for child in c_res.json().get("results", []):
            queue.append(child["id"])

while queue:
    current_id = queue.pop(0)
    if current_id in processed_ids:
        continue

    page_data = get_page_by_id(current_id)
    if not page_data:
        continue

    processed_ids.add(current_id)
    _, body_md = process_and_save_page(page_data)

    linked_titles = extract_referenced_titles(body_md)
    for title in linked_titles:
        if title not in downloaded_pages:
            linked_page_data = get_page_by_title(title)
            if linked_page_data and linked_page_data["id"] not in processed_ids:
                queue.append(linked_page_data["id"])

convert_external_links_to_local_md()
print("\nFinished exporting all linked pages and converting URLs to local Markdown links!")