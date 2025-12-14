#!/usr/bin/env python3
"""
Zotero API Client - Connect to Zotero and fetch all files/attachments.

This module uses the pyzotero library to interact with the Zotero API v3.
It provides functionality to:
- Connect to personal or group Zotero libraries
- Fetch all items from the library
- Retrieve and download file attachments (PDFs, etc.)
- List collections and their contents

Requirements:
    pip install pyzotero httpx

Setup:
    Option 1: Create a .keys file in the project directory with:
        zotero=YOUR_API_KEY
        zotero_library_id=YOUR_LIBRARY_ID (optional, will auto-discover)

    Option 2: Set environment variables:
       - ZOTERO_API_KEY
       - ZOTERO_LIBRARY_ID (optional, will auto-discover)
       - ZOTERO_LIBRARY_TYPE (optional, defaults to 'user')
"""

import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

import httpx

try:
    from pyzotero import zotero
except ImportError:
    raise ImportError("pyzotero is required. Install it with: pip install pyzotero")


# Path to keys file
KEYS_FILE = Path(__file__).parent / ".keys"


def load_keys_file(keys_path: Path = KEYS_FILE) -> Dict[str, str]:
    """Load key-value pairs from .keys file."""
    keys = {}
    if keys_path.exists():
        with open(keys_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    keys[key.strip().lower()] = value.strip()
    return keys


def get_library_id_from_api_key(api_key: str) -> Optional[str]:
    """
    Retrieve the user's library ID using the API key.

    The Zotero API provides key info at /keys/{key} which includes
    the userID associated with the key.
    """
    try:
        response = httpx.get(
            f"https://api.zotero.org/keys/{api_key}",
            headers={"Zotero-API-Version": "3"},
        )
        if response.status_code == 200:
            data = response.json()
            return str(data.get("userID"))
    except Exception as e:
        print(f"Warning: Could not auto-discover library ID: {e}")
    return None


@dataclass
class ZoteroConfig:
    """Configuration for Zotero API connection."""

    library_id: str
    api_key: str
    library_type: str = "user"  # 'user' or 'group'

    @classmethod
    def from_env(cls) -> "ZoteroConfig":
        """Create config from environment variables."""
        library_id = os.environ.get("ZOTERO_LIBRARY_ID")
        api_key = os.environ.get("ZOTERO_API_KEY")
        library_type = os.environ.get("ZOTERO_LIBRARY_TYPE", "user")

        if not library_id or not api_key:
            raise ValueError(
                "Missing required environment variables. "
                "Please set ZOTERO_LIBRARY_ID and ZOTERO_API_KEY"
            )

        return cls(library_id=library_id, api_key=api_key, library_type=library_type)

    @classmethod
    def from_keys_file(cls, keys_path: Path = KEYS_FILE) -> "ZoteroConfig":
        """
        Create config from .keys file.

        Expected format:
            zotero=YOUR_API_KEY
            zotero_library_id=YOUR_LIBRARY_ID (optional)
            zotero_library_type=user (optional)
        """
        keys = load_keys_file(keys_path)

        api_key = keys.get("zotero")
        if not api_key:
            raise ValueError(f"No 'zotero' API key found in {keys_path}")

        # Try to get library ID from file, or auto-discover it
        library_id = keys.get("zotero_library_id")
        if not library_id:
            print("Auto-discovering library ID from API key...")
            library_id = get_library_id_from_api_key(api_key)
            if not library_id:
                raise ValueError(
                    "Could not determine library ID. "
                    "Please add 'zotero_library_id=YOUR_ID' to .keys file"
                )
            print(f"Found library ID: {library_id}")

        library_type = keys.get("zotero_library_type", "user")

        return cls(library_id=library_id, api_key=api_key, library_type=library_type)

    @classmethod
    def auto_load(cls) -> "ZoteroConfig":
        """
        Auto-load config from .keys file or environment variables.
        Prefers .keys file if it exists.
        """
        if KEYS_FILE.exists():
            return cls.from_keys_file()
        return cls.from_env()


@dataclass
class ZoteroAttachment:
    """Represents a Zotero file attachment."""

    key: str
    title: str
    filename: str
    content_type: str
    link_mode: str
    parent_key: Optional[str] = None
    md5: Optional[str] = None
    mtime: Optional[int] = None

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> Optional["ZoteroAttachment"]:
        """Create attachment from Zotero item dict."""
        data = item.get("data", {})
        if data.get("itemType") != "attachment":
            return None

        return cls(
            key=data.get("key", ""),
            title=data.get("title", ""),
            filename=data.get("filename", ""),
            content_type=data.get("contentType", ""),
            link_mode=data.get("linkMode", ""),
            parent_key=data.get("parentItem"),
            md5=data.get("md5"),
            mtime=data.get("mtime"),
        )


@dataclass
class ZoteroItem:
    """Represents a Zotero library item."""

    key: str
    item_type: str
    title: str
    creators: List[Dict[str, str]] = field(default_factory=list)
    date: str = ""
    abstract: str = ""
    url: str = ""
    doi: str = ""
    tags: List[str] = field(default_factory=list)
    collections: List[str] = field(default_factory=list)
    attachments: List[ZoteroAttachment] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "ZoteroItem":
        """Create ZoteroItem from API response dict."""
        data = item.get("data", {})

        return cls(
            key=data.get("key", ""),
            item_type=data.get("itemType", ""),
            title=data.get("title", ""),
            creators=data.get("creators", []),
            date=data.get("date", ""),
            abstract=data.get("abstractNote", ""),
            url=data.get("url", ""),
            doi=data.get("DOI", ""),
            tags=[t.get("tag", "") for t in data.get("tags", [])],
            collections=data.get("collections", []),
            raw_data=data,
        )

    def get_authors_string(self) -> str:
        """Get formatted author string."""
        authors = []
        for creator in self.creators:
            if creator.get("creatorType") == "author":
                name = f"{creator.get('lastName', '')} {creator.get('firstName', '')}".strip()
                if not name and creator.get("name"):
                    name = creator.get("name", "")
                if name:
                    authors.append(name)
        return ", ".join(authors)


class ZoteroAPI:
    """
    Zotero API client for fetching library items and attachments.

    Example usage:
        # Using environment variables
        api = ZoteroAPI.from_env()

        # Or with explicit credentials
        api = ZoteroAPI(
            library_id="123456",
            api_key="your_api_key",
            library_type="user"
        )

        # Fetch all items
        items = api.get_all_items()

        # Get attachments
        attachments = api.get_all_attachments()

        # Download a file
        api.download_file("ITEMKEY123", "/path/to/save/")
    """

    def __init__(
        self,
        library_id: str,
        api_key: str,
        library_type: str = "user",
    ):
        """
        Initialize Zotero API client.

        Args:
            library_id: Your Zotero user ID or group ID
            api_key: Your Zotero API key
            library_type: 'user' for personal library, 'group' for shared library
        """
        self.library_id = library_id
        self.api_key = api_key
        self.library_type = library_type
        self._zot = zotero.Zotero(library_id, library_type, api_key)

    @classmethod
    def from_env(cls) -> "ZoteroAPI":
        """Create API client from environment variables."""
        config = ZoteroConfig.from_env()
        return cls(
            library_id=config.library_id,
            api_key=config.api_key,
            library_type=config.library_type,
        )

    @classmethod
    def from_config(cls, config: ZoteroConfig) -> "ZoteroAPI":
        """Create API client from config object."""
        return cls(
            library_id=config.library_id,
            api_key=config.api_key,
            library_type=config.library_type,
        )

    @classmethod
    def auto_load(cls) -> "ZoteroAPI":
        """
        Auto-load API client from .keys file or environment variables.
        Prefers .keys file if it exists.
        """
        config = ZoteroConfig.auto_load()
        return cls.from_config(config)

    def get_key_info(self) -> Dict[str, Any]:
        """Get information about the API key permissions."""
        return self._zot.key_info()

    def get_item_count(self) -> int:
        """Get total count of items in the library."""
        return self._zot.count_items()

    def get_items(self, limit: int = 100, start: int = 0) -> List[Dict[str, Any]]:
        """
        Get items from the library with pagination.

        Args:
            limit: Number of items to retrieve (max 100)
            start: Starting position for pagination

        Returns:
            List of item dictionaries
        """
        return self._zot.items(limit=limit, start=start)

    def get_all_items(self) -> List[ZoteroItem]:
        """
        Retrieve ALL items from the library.

        Uses the everything() wrapper to handle pagination automatically.

        Returns:
            List of ZoteroItem objects
        """
        all_items = self._zot.everything(self._zot.items())
        return [
            ZoteroItem.from_item(item)
            for item in all_items
            if item.get("data", {}).get("itemType") != "attachment"
        ]

    def get_top_items(self, limit: Optional[int] = None) -> List[ZoteroItem]:
        """
        Get top-level items (excludes child items like notes and attachments).

        Args:
            limit: Optional limit on number of items

        Returns:
            List of ZoteroItem objects
        """
        if limit:
            items = self._zot.top(limit=limit)
        else:
            items = self._zot.everything(self._zot.top())

        return [ZoteroItem.from_item(item) for item in items]

    def get_item(self, item_key: str) -> Optional[ZoteroItem]:
        """
        Get a single item by its key.

        Args:
            item_key: The Zotero item key

        Returns:
            ZoteroItem object or None if not found
        """
        try:
            items = self._zot.item(item_key)
            if items:
                return ZoteroItem.from_item(
                    items[0] if isinstance(items, list) else items
                )
        except Exception:
            pass
        return None

    def get_children(self, item_key: str) -> List[Dict[str, Any]]:
        """
        Get child items (attachments, notes) of a parent item.

        Args:
            item_key: The parent item's key

        Returns:
            List of child item dictionaries
        """
        return self._zot.children(item_key)

    def get_attachments_for_item(self, item_key: str) -> List[ZoteroAttachment]:
        """
        Get all attachments for a specific item.

        Args:
            item_key: The parent item's key

        Returns:
            List of ZoteroAttachment objects
        """
        children = self.get_children(item_key)
        attachments = []
        for child in children:
            attachment = ZoteroAttachment.from_item(child)
            if attachment:
                attachments.append(attachment)
        return attachments

    def get_all_attachments(self) -> List[ZoteroAttachment]:
        """
        Get ALL file attachments from the library.

        Returns:
            List of ZoteroAttachment objects
        """
        # Get all items including attachments
        all_items = self._zot.everything(self._zot.items())
        attachments = []

        for item in all_items:
            attachment = ZoteroAttachment.from_item(item)
            if attachment and attachment.filename:  # Only include items with files
                attachments.append(attachment)

        return attachments

    def get_file_content(self, item_key: str) -> bytes:
        """
        Get the raw binary content of an attachment file.

        Args:
            item_key: The attachment item's key

        Returns:
            Binary file content
        """
        return self._zot.file(item_key)

    def download_file(
        self,
        item_key: str,
        save_path: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> str:
        """
        Download an attachment file to disk.

        Args:
            item_key: The attachment item's key
            save_path: Directory to save the file (default: current directory)
            filename: Custom filename (default: use original filename from Zotero)

        Returns:
            Full path to the downloaded file
        """
        if save_path and filename:
            return self._zot.dump(item_key, filename, save_path)
        elif save_path:
            return self._zot.dump(item_key, path=save_path)
        elif filename:
            return self._zot.dump(item_key, filename=filename)
        else:
            return self._zot.dump(item_key)

    def download_all_files(
        self,
        save_directory: str,
        file_types: Optional[List[str]] = None,
        organize_by_collection: bool = False,
    ) -> Dict[str, str]:
        """
        Download all attachment files from the library.

        Args:
            save_directory: Directory to save files
            file_types: List of content types to include (e.g., ['application/pdf'])
                       If None, downloads all file types
            organize_by_collection: If True, organize files by collection folders

        Returns:
            Dictionary mapping item keys to downloaded file paths
        """
        save_path = Path(save_directory)
        save_path.mkdir(parents=True, exist_ok=True)

        attachments = self.get_all_attachments()
        downloaded = {}

        for attachment in attachments:
            # Filter by file type if specified
            if file_types and attachment.content_type not in file_types:
                continue

            try:
                file_path = self.download_file(
                    attachment.key,
                    save_path=str(save_path),
                )
                downloaded[attachment.key] = file_path
                print(f"Downloaded: {attachment.filename}")
            except Exception as e:
                print(f"Failed to download {attachment.filename}: {e}")

        return downloaded

    # Collection methods
    def get_collections(self) -> List[Dict[str, Any]]:
        """Get all collections in the library."""
        return self._zot.everything(self._zot.collections())

    def get_collection_items(self, collection_key: str) -> List[ZoteroItem]:
        """
        Get all items in a specific collection.

        Args:
            collection_key: The collection's key

        Returns:
            List of ZoteroItem objects
        """
        items = self._zot.everything(self._zot.collection_items(collection_key))
        return [
            ZoteroItem.from_item(item)
            for item in items
            if item.get("data", {}).get("itemType") != "attachment"
        ]

    # Search methods
    def search(
        self,
        query: str,
        item_type: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[ZoteroItem]:
        """
        Search the library.

        Args:
            query: Search query string
            item_type: Filter by item type (e.g., 'book', 'journalArticle')
            tag: Filter by tag

        Returns:
            List of matching ZoteroItem objects
        """
        kwargs = {"q": query}
        if item_type:
            kwargs["itemType"] = item_type
        if tag:
            kwargs["tag"] = tag

        items = self._zot.everything(self._zot.items(**kwargs))
        return [
            ZoteroItem.from_item(item)
            for item in items
            if item.get("data", {}).get("itemType") != "attachment"
        ]

    def get_items_with_attachments(self) -> List[ZoteroItem]:
        """
        Get all top-level items along with their attachments.

        Returns:
            List of ZoteroItem objects with populated attachments field
        """
        items = self.get_top_items()

        for item in items:
            item.attachments = self.get_attachments_for_item(item.key)

        return items

    def export_library_summary(self, output_path: str) -> None:
        """
        Export a JSON summary of the library.

        Args:
            output_path: Path for the output JSON file
        """
        items = self.get_items_with_attachments()

        summary = {
            "total_items": len(items),
            "total_attachments": sum(len(item.attachments) for item in items),
            "items": [
                {
                    "key": item.key,
                    "type": item.item_type,
                    "title": item.title,
                    "authors": item.get_authors_string(),
                    "date": item.date,
                    "doi": item.doi,
                    "tags": item.tags,
                    "attachments": [
                        {
                            "key": att.key,
                            "filename": att.filename,
                            "content_type": att.content_type,
                        }
                        for att in item.attachments
                    ],
                }
                for item in items
            ],
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        print(f"Library summary exported to: {output_path}")


def main():
    """Example usage of the ZoteroAPI."""
    # Auto-load config from .keys file or environment
    try:
        api = ZoteroAPI.auto_load()
    except ValueError as e:
        print(f"Error: {e}")
        print("\nTo use this script, create a .keys file with:")
        print("  zotero=YOUR_API_KEY")
        print("  zotero_library_id=YOUR_LIBRARY_ID  (optional, will auto-discover)")
        print("\nOr set environment variables:")
        print("  export ZOTERO_API_KEY='your_api_key'")
        print("  export ZOTERO_LIBRARY_ID='your_user_id'")
        return

    # Get library info
    print("=" * 60)
    print("ZOTERO LIBRARY SUMMARY")
    print("=" * 60)

    item_count = api.get_item_count()
    print(f"\nTotal items in library: {item_count}")

    # Get all collections
    collections = api.get_collections()
    print(f"Total collections: {len(collections)}")
    for col in collections[:5]:  # Show first 5
        print(f"  - {col['data']['name']} (key: {col['data']['key']})")
    if len(collections) > 5:
        print(f"  ... and {len(collections) - 5} more")
    exit()

    # Get recent items
    print("\n" + "-" * 40)
    print("RECENT ITEMS (top 5):")
    print("-" * 40)

    recent_items = api.get_top_items(limit=5)
    for item in recent_items:
        print(
            f"\n• {item.title[:60]}..." if len(item.title) > 60 else f"\n• {item.title}"
        )
        print(f"  Type: {item.item_type}")
        print(f"  Authors: {item.get_authors_string()}")
        print(f"  Key: {item.key}")

    # Get all attachments
    print("\n" + "-" * 40)
    print("ATTACHMENTS SUMMARY:")
    print("-" * 40)

    attachments = api.get_all_attachments()
    print(f"\nTotal attachments with files: {len(attachments)}")

    # Group by content type
    by_type: Dict[str, int] = {}
    for att in attachments:
        ct = att.content_type or "unknown"
        by_type[ct] = by_type.get(ct, 0) + 1

    print("\nAttachments by type:")
    for content_type, count in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  {content_type}: {count}")

    # Show some attachment examples
    print("\nSample attachments (first 5):")
    for att in attachments[:5]:
        print(f"  - {att.filename} ({att.content_type})")

    print("\n" + "=" * 60)
    print("API client ready! You can now:")
    print("  - api.get_all_items() - Get all library items")
    print("  - api.get_all_attachments() - Get all file attachments")
    print("  - api.download_file('KEY123', '/path/') - Download a file")
    print("  - api.download_all_files('/save/dir/') - Download all files")
    print("  - api.export_library_summary('output.json') - Export summary")
    print("=" * 60)


if __name__ == "__main__":
    main()
