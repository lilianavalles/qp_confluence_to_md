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
TEMPLATES_DIR = os.path.join(OUTPUT_DIR, "templates")
SEED_PAGE_ID = "465639889"

headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# Tracks page metadata: page_id / title -> { "filename": str, "folder": str }
page_registry = {}
seed_child_ids = set()

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

def process_and_save_page(page_data, folder=""):
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

    # Register page info for relative link resolution
    page_info = {"filename": file_name, "folder": folder}
    page_registry[page_id] = page_info
    page_registry[title] = page_info
    
    print(f"Saved [{folder or 'root'}]: {file_path}")
    return file_path, markdown_body

def extract_referenced_titles(markdown_text):
    pattern = r"https?://nhsd-confluence\.digital\.nhs\.uk/display/DAT/([^)\s]+)"
    matches = re.findall(pattern, markdown_text)
    return [m.replace("+", " ") for m in matches]

def convert_external_links_to_local_md():
    print("\nCalculating relative markdown links across all files...")
    
    # Walk through root and templates directory
    for root, _, files in os.walk(OUTPUT_DIR):
        for file_name in files:
            if not file_name.endswith(".md"):
                continue
            
            file_path = os.path.join(root, file_name)
            
            # Determine current file's folder relative to OUTPUT_DIR
            rel_dir = os.path.relpath(root, OUTPUT_DIR)
            current_folder = "" if rel_dir == "." else rel_dir

            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            def replace_link(match):
                anchor_text = match.group(1)
                raw_target = match.group(2).replace("+", " ")
                
                if raw_target in page_registry:
                    target_info = page_registry[raw_target]
                    target_folder = target_info["folder"]
                    target_file = target_info["filename"]
                    
                    # Compute relative path between current_folder and target_folder
                    if current_folder == target_folder:
                        rel_path = f"./{target_file}"
                    elif current_folder == "" and target_folder == "templates":
                        rel_path = f"./templates/{target_file}"
                    elif current_folder == "templates" and target_folder == "":
                        rel_path = f"../{target_file}"
                    else:
                        rel_path = f"./{target_file}" # fallback
                        
                    return f"[{anchor_text}]({requests.utils.quote(rel_path, safe='./')})"
                
                return match.group(0)

            pattern = r"\[([^\]]+)\]\(https?://nhsd-confluence\.digital\.nhs\.uk/display/DAT/([^)]+)\)"
            updated_content = re.sub(pattern, replace_link, content)

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(updated_content)

# --- EXECUTION ---
processed_ids = set()
queue = []

# 1. Process and save the Seed Parent Page at the root level
print(f"Fetching seed parent page ID: {SEED_PAGE_ID}...")
seed_data = get_page_by_id(SEED_PAGE_ID)
if seed_data:
    _, seed_body_md = process_and_save_page(seed_data, folder="")
    processed_ids.add(SEED_PAGE_ID)
    
    # Extract links from seed page to see if they point to templates
    linked_titles = extract_referenced_titles(seed_body_md)
    for title in linked_titles:
        if title not in page_registry:
            linked_page_data = get_page_by_title(title)
            if linked_page_data and linked_page_data["id"] not in processed_ids:
                # Discovered via link -> send to templates folder
                queue.append((linked_page_data["id"], "templates"))

    # 2. Fetch seed page children and keep them at the root level (under parent)
    child_url = f"{BASE_URL}/rest/api/content/{SEED_PAGE_ID}/child/page"
    c_res = requests.get(child_url, headers=headers)
    if c_res.status_code == 200:
        for child in c_res.json().get("results", []):
            seed_child_ids.add(child["id"])
            if child["id"] not in processed_ids:
                queue.append((child["id"], ""))

# 3. Main queue processing loop
while queue:
    current_id, target_folder = queue.pop(0)
    if current_id in processed_ids:
        continue

    page_data = get_page_by_id(current_id)
    if not page_data:
        continue

    processed_ids.add(current_id)
    _, body_md = process_and_save_page(page_data, folder=target_folder)

    # Find further linked pages
    linked_titles = extract_referenced_titles(body_md)
    for title in linked_titles:
        if title not in page_registry:
            linked_page_data = get_page_by_title(title)
            if linked_page_data and linked_page_data["id"] not in processed_ids:
                # Anything discovered via general links goes into templates
                queue.append((linked_page_data["id"], "templates"))

# 4. Post-process: Convert all Confluence URLs to smart local relative markdown links
convert_external_links_to_local_md()

print("\nFinished exporting! Parent and children are at root, templates are in the templates/ folder.")

# import os
# import re
# import requests
# from bs4 import BeautifulSoup
# from markdownify import markdownify as md
# from dotenv import load_dotenv

# load_dotenv()

# BASE_URL = os.getenv("CONFLUENCE_BASE_URL", "https://nhsd-confluence.digital.nhs.uk").rstrip("/")
# TOKEN = os.getenv("CONFLUENCE_API_TOKEN")
# SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY", "DAT")
# OUTPUT_DIR = "./exported_md"
# SEED_PAGE_ID = "465639889"

# headers = {
#     "Authorization": f"Bearer {TOKEN}",
#     "Content-Type": "application/json"
# }

# os.makedirs(OUTPUT_DIR, exist_ok=True)
# downloaded_pages = {}

# def sanitize_filename(title):
#     return re.sub(r'[\\/*?:"<>|]', "-", title).strip()

# def get_page_by_id(page_id):
#     url = f"{BASE_URL}/rest/api/content/{page_id}?expand=body.storage"
#     res = requests.get(url, headers=headers)
#     if res.status_code == 200:
#         return res.json()
#     return None

# def get_page_by_title(title):
#     url = f"{BASE_URL}/rest/api/content?spaceKey={SPACE_KEY}&title={requests.utils.quote(title)}&expand=body.storage"
#     res = requests.get(url, headers=headers)
#     if res.status_code == 200:
#         results = res.json().get("results", [])
#         return results[0] if results else None
#     return None

# def preprocess_confluence_html(html_content):
#     soup = BeautifulSoup(html_content, "html.parser")
#     for ac_link in soup.find_all(["ac:link", "link"]):
#         ri_page = ac_link.find(["ri:page", "page"])
#         link_text_el = ac_link.find(["ac:plain-text-link-body", "ac:link-body", "plain-text-link-body", "link-body"])
        
#         page_title = None
#         if ri_page and ri_page.has_attr("ri:content-title"):
#             page_title = ri_page["ri:content-title"]
#         elif ri_page and ri_page.has_attr("content-title"):
#             page_title = ri_page["content-title"]
            
#         display_text = link_text_el.get_text(strip=True) if link_text_el else page_title
        
#         if display_text and page_title:
#             clean_link = soup.new_tag("a", href=f"{BASE_URL}/display/{SPACE_KEY}/{page_title.replace(' ', '+')}")
#             clean_link.string = display_text
#             ac_link.replace_with(clean_link)
#         elif display_text:
#             ac_link.replace_with(display_text)
            
#     return str(soup)

# def process_and_save_page(page_data):
#     page_id = page_data["id"]
#     title = page_data["title"]
#     clean_title = sanitize_filename(title)
#     file_name = f"{clean_title}.md"
#     file_path = os.path.join(OUTPUT_DIR, file_name)

#     html_body = page_data.get("body", {}).get("storage", {}).get("value", "")
#     cleaned_html = preprocess_confluence_html(html_body)
#     markdown_body = md(cleaned_html, heading_style="ATX", strip=['script', 'style'])

#     md_content = f"# {title}\n\n{markdown_body}"
    
#     with open(file_path, "w", encoding="utf-8") as f:
#         f.write(md_content)

#     downloaded_pages[page_id] = file_name
#     downloaded_pages[title] = file_name
#     print(f"Saved: {file_path}")
#     return file_path, markdown_body

# def extract_referenced_titles(markdown_text):
#     pattern = r"https?://nhsd-confluence\.digital\.nhs\.uk/display/DAT/([^)\s]+)"
#     matches = re.findall(pattern, markdown_text)
#     return [m.replace("+", " ") for m in matches]

# def convert_external_links_to_local_md():
#     print("\nLinking Markdown files together with local relative paths...")
#     for file_name in os.listdir(OUTPUT_DIR):
#         if not file_name.endswith(".md"):
#             continue
        
#         file_path = os.path.join(OUTPUT_DIR, file_name)
#         with open(file_path, "r", encoding="utf-8") as f:
#             content = f.read()

#         def replace_link(match):
#             anchor_text = match.group(1)
#             raw_target = match.group(2).replace("+", " ")
#             if raw_target in downloaded_pages:
#                 local_md = downloaded_pages[raw_target]
#                 return f"[{anchor_text}](./{requests.utils.quote(local_md)})"
#             return match.group(0)

#         pattern = r"\[([^\]]+)\]\(https?://nhsd-confluence\.digital\.nhs\.uk/display/DAT/([^)]+)\)"
#         updated_content = re.sub(pattern, replace_link, content)

#         with open(file_path, "w", encoding="utf-8") as f:
#             f.write(updated_content)


# # --- EXECUTION ---
# processed_ids = set()
# queue = []

# # 1. Process and save the Seed Page first
# print(f"Fetching seed parent page ID: {SEED_PAGE_ID}...")
# seed_data = get_page_by_id(SEED_PAGE_ID)
# if seed_data:
#     _, seed_body_md = process_and_save_page(seed_data)
#     processed_ids.add(SEED_PAGE_ID)
    
#     # Also discover linked pages inside the seed page immediately
#     linked_titles = extract_referenced_titles(seed_body_md)
#     for title in linked_titles:
#         if title not in downloaded_pages:
#             linked_page_data = get_page_by_title(title)
#             if linked_page_data and linked_page_data["id"] not in processed_ids:
#                 queue.append(linked_page_data["id"])

#     # 2. Fetch seed page children and add them to the queue
#     child_url = f"{BASE_URL}/rest/api/content/{SEED_PAGE_ID}/child/page"
#     c_res = requests.get(child_url, headers=headers)
#     if c_res.status_code == 200:
#         for child in c_res.json().get("results", []):
#             if child["id"] not in processed_ids:
#                 queue.append(child["id"])

# # 3. Main queue processing loop (handles children and recursively linked pages)
# while queue:
#     current_id = queue.pop(0)
#     if current_id in processed_ids:
#         continue

#     page_data = get_page_by_id(current_id)
#     if not page_data:
#         continue

#     processed_ids.add(current_id)
#     _, body_md = process_and_save_page(page_data)

#     # Find linked pages and add them to queue
#     linked_titles = extract_referenced_titles(body_md)
#     for title in linked_titles:
#         if title not in downloaded_pages:
#             linked_page_data = get_page_by_title(title)
#             if linked_page_data and linked_page_data["id"] not in processed_ids:
#                 queue.append(linked_page_data["id"])

# # 4. Post-process: Convert all Confluence URLs to local relative .md links
# convert_external_links_to_local_md()

# print("\nFinished exporting seed page, children, linked pages, and updating links!")

def generate_mkdocs_nav(seed_title):
    print("\nGenerating MkDocs navigation YAML snippet...")
    nav_lines = ["nav:"]
    
    # 1. Add Seed page first
    seed_filename = f"{sanitize_filename(seed_title)}.md"
    nav_lines.append(f"  - Quality Framework: {seed_filename}")
    
    # 2. Add root-level children
    root_files = []
    template_files = []
    
    for filename in os.listdir(OUTPUT_DIR):
        if not filename.endswith(".md") or filename == seed_filename:
            continue
        root_files.append(filename)
        
    for filename in os.listdir(TEMPLATES_DIR):
        if filename.endswith(".md"):
            template_files.append(filename)
            
    # Sort them nicely
    root_files.sort()
    template_files.sort()
    
    for f in root_files:
        # Convert filename back to a readable title (remove extension)
        title = os.path.splitext(f)[0]
        nav_lines.append(f"  - {title}: {f}")
        
    # 3. Add Templates section if any exist
    if template_files:
        nav_lines.append("  - Templates:")
        for f in template_files:
            title = os.path.splitext(f)[0]
            nav_lines.append(f"    - {title}: templates/{f}")
            
    # Save to a snippet file
    snippet_path = "generated_nav.yml"
    with open(snippet_path, "w", encoding="utf-8") as f:
        f.write("\n".join(nav_lines))
        
    print(f"MkDocs navigation snippet saved to '{snippet_path}'!")

    # get it:
generate_mkdocs_nav("DS Quality Framework")