# Zotero Manager

Python tools for managing Zotero libraries and attachments.

## Scripts

- **zotapi.py** - Fetch data via Zotero API (requires internet, API key)
- **zotdb.py** - Read local Zotero SQLite database (offline, faster)
- **zotcopy.py** - Copy/deduplicate files (requires Python 3.13+)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
pip install pyzotero httpx
```

### API Configuration

Create `.keys` file:
```
zotero=YOUR_API_KEY
zotero_library_id=YOUR_LIBRARY_ID
```

Or set environment variables:
```bash
export ZOTERO_API_KEY=your_key
export ZOTERO_LIBRARY_ID=your_id
```

## Usage

```bash
./zotapi.py    # Access via API
./zotdb.py     # Access local database
./zotcopy.py   # Copy and deduplicate files
```
