# qp_confluence_to_md

This repository provides a Python-based tool to export specific Confluence pages (starting from the **DS Quality Framework** and automatically tracking child and linked pages) from the **DAT** space into clean Markdown files. 

It preserves tables and converts Confluence internal links into local relative Markdown links (`[Page Title](./Page%20Title.md)`), making it ideal for publishing documentation sites like GitHub Pages.

---

## Features

- **Automated Discovery:** Fetches a seed parent page, its children, and recursively grabs any referenced Confluence pages.
- **Table Preservation:** Converts Confluence grids into standard, readable Markdown tables.
- **Local Linking:** Automatically rewrites Confluence URLs to relative local `.md` links for seamless cross-navigation.
- **Secure Authentication:** Uses a personal `.env` configuration file to keep API tokens safe and out of version control, once cloned, it is the only bit that will need updating

---

## Prerequisites

- **Python 3.10+** installed on your machine.
- A **Confluence Personal Access Token (PAT)** from your NHS Confluence account.

---

## Setup Instructions

### 1. Clone the Repository
Open your terminal and clone your new repository, then navigate into the folder:
```bash
git clone [https://github.com/lilianavalles/qp_confluence_to_md.git](https://github.com/YOUR-GITHUB-USERNAME/qp_confluence_to_md.git)
cd qp_confluence_to_md
```

### 2. Install Dependencies
Install the required Python libraries using pip:
```pip install -r requirements.txt

### Step 3: Configure Your Environment Variables
Bash
cp .env.example .env
(After running this, open your new .env file in VS Code and fill in your actual Confluence Personal Access Token).

### Step 4: Run the Exporter
Bash
python export_all_linked.py

### Step 5: Updating Content (When Confluence Changes)
Bash
python export_all_linked.py
