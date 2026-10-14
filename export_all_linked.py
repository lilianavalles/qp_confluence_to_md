import os
import re
import requests
from urllib.parse import unquote
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CONFLUENCE_BASE_URL", "https://nhsd-confluence.digital.nhs.uk").rstrip("/")
TOKEN = os.getenv("CONFLUENCE_API_TOKEN")
SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY", "DAT")

# --- CONFIGURE HERE ---
OUTPUT_DIR = "./qaf"
TEMPLATES_SUBDIR = "templates"
GUIDANCE_SUBDIR = "guidance"

SEED_PAGE_ID = "465639889"           # DS Quality Framework (Root level)
TEMPLATES_PARENT_ID = "1532337490"             # Set if bounding templates by parent ID
GUIDANCE_PARENT_ID = "1429693086"      # Quality Framework Guides page ID

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, TEMPLATES_SUBDIR), exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, GUIDANCE_SUBDIR), exist_ok=True)

page_registry = {}
allowed_page_ids = set()

def sanitize_filename(title):
    """Removes invalid filesystem characters from Confluence page titles."""
    return re.sub(r'[\\/*?:"<>|]', "-", title).strip()

def get_page_by_id(page_id):
    """Fetches a specific Confluence page's storage format and details using its ID."""
    url = f"{BASE_URL}/rest/api/content/{page_id}?expand=body.storage"
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        return res.json()
    return None

def get_child_pages(parent_id):
    """Retrieves all direct child pages for a given Confluence parent ID."""
    url = f"{BASE_URL}/rest/api/content/{parent_id}/child/page"
    res = requests.get(url, headers=headers)
    if res.status_code == 200:
        return res.json().get("results", [])
    return []

def preprocess_confluence_html(html_content):
    """Converts internal Confluence XHTML link macros into standard HTML anchor tags."""
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

def process_and_save_page(page_data, folder=""):
    """Converts Confluence HTML content to Markdown, saves it to the appropriate subfolder, and registers metadata."""
    page_id = page_data["id"]
    title = page_data["title"]
    clean_title = sanitize_filename(title)
    file_name = f"{clean_title}.md"
    
    target_dir = os.path.join(OUTPUT_DIR, folder) if folder else OUTPUT_DIR
    file_path = os.path.join(target_dir, file_name)

    html_body = page_data.get("body", {}).get("storage", {}).get("value", "")
    cleaned_html = preprocess_confluence_html(html_body)
    markdown_body = md(cleaned_html, heading_style="ATX", strip=['script', 'style'])

    md_content = f"# {title}\n\n{markdown_body}"
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    page_info = {"filename": file_name, "folder": folder, "title": title}
    page_registry[str(page_id)] = page_info
    page_registry[title] = page_info
    page_registry[title.lower()] = page_info
    
    print(f"Saved [{folder or 'root'}]: {file_path}")
    return file_path

# --- SCOPE DISCOVERY ---
print("Discovering bounded Confluence pages...")

# 1. Seed Page + its direct children -> Root Folder
if SEED_PAGE_ID:
    seed_data = get_page_by_id(SEED_PAGE_ID)
    if seed_data:
        process_and_save_page(seed_data, folder="")
        allowed_page_ids.add(SEED_PAGE_ID)
        
        for child in get_child_pages(SEED_PAGE_ID):
            child_data = get_page_by_id(child["id"])
            if child_data:
                process_and_save_page(child_data, folder="")
                allowed_page_ids.add(child["id"])

# 2. Guidance Parent + its children -> Guidance Folder
if GUIDANCE_PARENT_ID:
    guidance_parent = get_page_by_id(GUIDANCE_PARENT_ID)
    if guidance_parent:
        process_and_save_page(guidance_parent, folder=GUIDANCE_SUBDIR)
        allowed_page_ids.add(GUIDANCE_PARENT_ID)
        
        for child in get_child_pages(GUIDANCE_PARENT_ID):
            child_data = get_page_by_id(child["id"])
            if child_data:
                process_and_save_page(child_data, folder=GUIDANCE_SUBDIR)
                allowed_page_ids.add(child["id"])

# 3. Templates Parent + its children -> Templates Folder
if TEMPLATES_PARENT_ID:
    templates_parent = get_page_by_id(TEMPLATES_PARENT_ID)
    if templates_parent:
        process_and_save_page(templates_parent, folder=TEMPLATES_SUBDIR)
        allowed_page_ids.add(TEMPLATES_PARENT_ID)
        
        for child in get_child_pages(TEMPLATES_PARENT_ID):
            child_data = get_page_by_id(child["id"])
            if child_data:
                process_and_save_page(child_data, folder=TEMPLATES_SUBDIR)
                allowed_page_ids.add(child["id"])

# --- LINK CONVERSION & CLEANUP ---
def convert_and_clean_links():
    """Scans exported Markdown files, updates in-scope Confluence links to relative local paths, and strips out-of-scope Confluence links."""
    print("\nProcessing links: rewriting in-scope links, stripping out-of-scope Confluence links...")
    
    for root, _, files in os.walk(OUTPUT_DIR):
        for file_name in files:
            if not file_name.endswith(".md"):
                continue
            
            file_path = os.path.join(root, file_name)
            rel_dir = os.path.relpath(root, OUTPUT_DIR)
            current_folder = "" if rel_dir == "." else rel_dir

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            def process_link(match):
                anchor_text = match.group(1)
                url = match.group(2)
                
                is_confluence = "nhsd-confluence.digital.nhs.uk" in url
                
                if not is_confluence:
                    return match.group(0) # Keep external links untouched
                
                target_info = None
                id_match = re.search(r'/pages/(\d+)/', url)
                if id_match:
                    page_id = id_match.group(1)
                    target_info = page_registry.get(page_id)
                else:
                    display_match = re.search(r'/display/[^/]+/([^?\s#)]+)', url)
                    if display_match:
                        raw_target = unquote(display_match.group(1)).replace("+", " ")
                        target_info = page_registry.get(raw_target) or page_registry.get(raw_target.lower())
                
                if target_info:
                    target_folder = target_info["folder"]
                    target_file = target_info["filename"]
                    
                    if current_folder == target_folder:
                        rel_path = f"./{target_file}"
                    elif current_folder == "" and target_folder == TEMPLATES_SUBDIR:
                        rel_path = f"./{TEMPLATES_SUBDIR}/{target_file}"
                    elif current_folder == "" and target_folder == GUIDANCE_SUBDIR:
                        rel_path = f"./{GUIDANCE_SUBDIR}/{target_file}"
                    elif current_folder == TEMPLATES_SUBDIR and target_folder == "":
                        rel_path = f"../{target_file}"
                    elif current_folder == GUIDANCE_SUBDIR and target_folder == "":
                        rel_path = f"../{target_file}"
                    else:
                        rel_path = f"../{target_folder}/{target_file}"
                        
                    return f"[{anchor_text}]({requests.utils.quote(rel_path, safe='./')})"
                else:
                    return anchor_text # Strip out-of-scope Confluence links, keeping text only

            pattern = r"\[([^\]]+)\]\((https?://[^\s)]+)\)"
            updated_content = re.sub(pattern, process_link, content)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(updated_content)

convert_and_clean_links()

# --- NAV GENERATION ---
def generate_mkdocs_nav(seed_title, folder_prefix="qaf"):
    """Generates a hierarchical MkDocs navigation YAML snippet based on exported folders and files."""
    print("\nGenerating MkDocs navigation snippet...")
    nav_lines = ["nav:", "  - Quality Framework:"]
    
    seed_filename = f"{sanitize_filename(seed_title)}.md"
    nav_lines.append(f"    - Quality Framework: {folder_prefix}/{seed_filename}")
    
    root_files = []
    guidance_files = []
    template_files = []
    
    if os.path.exists(OUTPUT_DIR):
        for filename in os.listdir(OUTPUT_DIR):
            if filename.endswith(".md") and filename != seed_filename:
                root_files.append(filename)
                
    g_dir = os.path.join(OUTPUT_DIR, GUIDANCE_SUBDIR)
    if os.path.exists(g_dir):
        for filename in os.listdir(g_dir):
            if filename.endswith(".md"):
                guidance_files.append(filename)
                
    t_dir = os.path.join(OUTPUT_DIR, TEMPLATES_SUBDIR)
    if os.path.exists(t_dir):
        for filename in os.listdir(t_dir):
            if filename.endswith(".md"):
                template_files.append(filename)
                
    root_files.sort()
    guidance_files.sort()
    template_files.sort()
    
    for f in root_files:
        title = os.path.splitext(f)[0]
        nav_lines.append(f"    - {title}: {folder_prefix}/{f}")
        
    if guidance_files:
        nav_lines.append("  - Guidance:")
        for f in guidance_files:
            title = os.path.splitext(f)[0]
            nav_lines.append(f"    - {title}: {folder_prefix}/{GUIDANCE_SUBDIR}/{f}")
            
    if template_files:
        nav_lines.append("  - Templates:")
        for f in template_files:
            title = os.path.splitext(f)[0]
            nav_lines.append(f"    - {title}: {folder_prefix}/{TEMPLATES_SUBDIR}/{f}")
            
    snippet_path = "generated_nav.yml"
    with open(snippet_path, "w", encoding="utf-8") as f:
        f.write("\n".join(nav_lines))
        
    print(f"MkDocs navigation snippet saved to '{snippet_path}'!")

generate_mkdocs_nav("DS Quality Framework", folder_prefix="qaf")
print("\nExport, scope confinement, and link cleanup completed successfully!")