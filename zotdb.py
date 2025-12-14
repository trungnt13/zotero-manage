#!/usr/bin/env python3
"""
Zotero Local Database Reader - Access Zotero data directly from SQLite.

Advantages over API:
- No internet connection required
- No API rate limits
- Faster for large libraries
- Access to all local data

Disadvantages:
- Requires Zotero to be installed locally
- Database might be locked if Zotero is running
- Schema may change between Zotero versions
"""

import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
import shutil
import tempfile
import platform
import os


def get_default_zotero_path() -> Path:
    """Get the default Zotero data directory path for any OS."""
    home = Path.home()
    system = platform.system()

    if system == "Darwin":  # macOS
        return home / "Zotero"
    elif system == "Windows":
        # Primary location
        zotero_path = home / "Zotero"
        if zotero_path.exists():
            return zotero_path
        # Alternative: check AppData (older Zotero versions)
        appdata = Path(os.environ.get("APPDATA", ""))
        if appdata:
            alt_path = appdata / "Zotero" / "Zotero"
            if alt_path.exists():
                return alt_path
        return zotero_path  # Default to primary
    else:  # Linux and others
        return home / "Zotero"


def get_zotero_db_path(zotero_dir: Optional[Path] = None) -> Path:
    """Get path to zotero.sqlite database."""
    if zotero_dir is None:
        zotero_dir = get_default_zotero_path()
    return zotero_dir / "zotero.sqlite"


@dataclass
class ZoteroCollection:
    """Represents a collection from the local Zotero database."""

    collection_id: int
    key: str
    name: str
    parent_id: Optional[int] = None
    parent_key: Optional[str] = None
    children: List["ZoteroCollection"] = field(default_factory=list)

    def get_full_path(self, collections_map: Dict[int, "ZoteroCollection"]) -> str:
        """Get the full path of the collection (e.g., 'Parent / Child / Grandchild')."""
        path_parts = [self.name]
        current = self
        while current.parent_id is not None and current.parent_id in collections_map:
            current = collections_map[current.parent_id]
            path_parts.insert(0, current.name)
        return " / ".join(path_parts)


@dataclass
class LocalZoteroItem:
    """Represents an item from the local Zotero database."""

    item_id: int
    key: str
    item_type: str
    title: str = ""
    creators: List[Dict[str, str]] = field(default_factory=list)
    date: str = ""
    abstract: str = ""
    doi: str = ""
    url: str = ""
    tags: List[str] = field(default_factory=list)
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    collections: List[Dict[str, Any]] = field(
        default_factory=list
    )  # Direct collections
    collection_paths: List[str] = field(
        default_factory=list
    )  # Full paths including parents


class ZoteroLocalDB:
    """
    Read-only access to local Zotero SQLite database.

    Example usage:
        db = ZoteroLocalDB()
        items = db.get_all_items()
        attachments = db.get_all_attachments()
    """

    def __init__(self, db_path: Optional[Path] = None, copy_db: bool = True):
        """
        Initialize local database reader.

        Args:
            db_path: Path to zotero.sqlite (auto-detected if None)
            copy_db: If True, work on a copy to avoid lock issues
        """
        self.original_db_path = db_path or get_zotero_db_path()

        if not self.original_db_path.exists():
            raise FileNotFoundError(
                f"Zotero database not found at: {self.original_db_path}\n"
                "Make sure Zotero is installed and has been run at least once."
            )

        self.copy_db = copy_db
        self._temp_dir = None
        self._db_path = self._prepare_db()
        self._conn: Optional[sqlite3.Connection] = None
        self._collections_map: Optional[Dict[int, ZoteroCollection]] = None

    def _prepare_db(self) -> Path:
        """Prepare database for reading (optionally copy to avoid locks)."""
        if self.copy_db:
            self._temp_dir = tempfile.mkdtemp()
            temp_db = Path(self._temp_dir) / "zotero.sqlite"
            shutil.copy2(self.original_db_path, temp_db)
            return temp_db
        return self.original_db_path

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        """Close database connection and cleanup."""
        if self._conn:
            self._conn.close()
            self._conn = None
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_item_count(self) -> int:
        """Get total number of items."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT COUNT(*) FROM items 
            WHERE itemID NOT IN (SELECT itemID FROM deletedItems)
        """
        )
        return cursor.fetchone()[0]

    def _get_collections_map(self) -> Dict[int, ZoteroCollection]:
        """Get or build the collections map (cached)."""
        if self._collections_map is None:
            self._collections_map = self._build_collections_map()
        return self._collections_map

    def _build_collections_map(self) -> Dict[int, ZoteroCollection]:
        """Build a map of all collections indexed by collection ID."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT collectionID, collectionName, parentCollectionID, key
            FROM collections
        """
        )

        collections_map: Dict[int, ZoteroCollection] = {}
        for row in cursor.fetchall():
            collection = ZoteroCollection(
                collection_id=row["collectionID"],
                key=row["key"],
                name=row["collectionName"],
                parent_id=row["parentCollectionID"],
            )
            collections_map[collection.collection_id] = collection

        # Set parent keys and build children lists
        for collection in collections_map.values():
            if (
                collection.parent_id is not None
                and collection.parent_id in collections_map
            ):
                parent = collections_map[collection.parent_id]
                collection.parent_key = parent.key
                parent.children.append(collection)

        return collections_map

    def get_collections_tree(self) -> List[ZoteroCollection]:
        """
        Get all collections as a tree structure.

        Returns:
            List of root collections (those without parents), each with nested children
        """
        collections_map = self._get_collections_map()
        return [c for c in collections_map.values() if c.parent_id is None]

    def get_collections(self) -> List[Dict[str, Any]]:
        """Get all collections with hierarchy information."""
        collections_map = self._get_collections_map()

        return [
            {
                "id": c.collection_id,
                "name": c.name,
                "parent_id": c.parent_id,
                "key": c.key,
                "parent_key": c.parent_key,
                "full_path": c.get_full_path(collections_map),
                "children_count": len(c.children),
            }
            for c in collections_map.values()
        ]

    def _get_item_collections(self, item_id: int) -> List[Dict[str, Any]]:
        """Get collections for an item with full hierarchy information."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT c.collectionID, c.collectionName, c.parentCollectionID, c.key
            FROM collectionItems ci
            JOIN collections c ON ci.collectionID = c.collectionID
            WHERE ci.itemID = ?
        """,
            (item_id,),
        )

        collections_map = self._get_collections_map()
        collections = []

        for row in cursor.fetchall():
            collection_id = row["collectionID"]
            collection = collections_map.get(collection_id)

            if collection:
                collections.append(
                    {
                        "id": collection.collection_id,
                        "key": collection.key,
                        "name": collection.name,
                        "parent_id": collection.parent_id,
                        "parent_key": collection.parent_key,
                        "full_path": collection.get_full_path(collections_map),
                    }
                )

        return collections

    def _get_item_collection_paths(self, item_id: int) -> List[str]:
        """Get full collection paths for an item."""
        collections = self._get_item_collections(item_id)
        return [c["full_path"] for c in collections]

    def get_all_items(self) -> List[LocalZoteroItem]:
        """Get all library items (excluding attachments and notes)."""
        conn = self._get_connection()

        # Get items with their types
        cursor = conn.execute(
            """
            SELECT i.itemID, i.key, it.typeName
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
            AND it.typeName NOT IN ('attachment', 'note')
        """
        )

        items = []
        for row in cursor.fetchall():
            item = LocalZoteroItem(
                item_id=row["itemID"], key=row["key"], item_type=row["typeName"]
            )

            # Get item data (title, date, etc.)
            self._populate_item_data(item)

            # Get creators
            item.creators = self._get_item_creators(item.item_id)

            # Get tags
            item.tags = self._get_item_tags(item.item_id)

            # Get attachments
            item.attachments = self._get_item_attachments(item.item_id)

            # Get collections
            item.collections = self._get_item_collections(item.item_id)
            item.collection_paths = self._get_item_collection_paths(item.item_id)

            items.append(item)

        return items

    def get_items_in_collection(
        self, collection_key: str, include_subcollections: bool = False
    ) -> List[LocalZoteroItem]:
        """
        Get all items in a specific collection.

        Args:
            collection_key: The collection's key
            include_subcollections: If True, include items from subcollections

        Returns:
            List of items in the collection
        """
        conn = self._get_connection()
        collections_map = self._get_collections_map()

        # Find the collection by key
        target_collection = None
        for c in collections_map.values():
            if c.key == collection_key:
                target_collection = c
                break

        if target_collection is None:
            return []

        # Get collection IDs to search
        collection_ids = [target_collection.collection_id]

        if include_subcollections:
            # Recursively get all subcollection IDs
            def get_descendant_ids(collection: ZoteroCollection) -> List[int]:
                ids = []
                for child in collection.children:
                    ids.append(child.collection_id)
                    ids.extend(get_descendant_ids(child))
                return ids

            collection_ids.extend(get_descendant_ids(target_collection))

        # Query items in these collections
        placeholders = ",".join("?" * len(collection_ids))
        cursor = conn.execute(
            f"""
            SELECT DISTINCT i.itemID, i.key, it.typeName
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            JOIN collectionItems ci ON i.itemID = ci.itemID
            WHERE ci.collectionID IN ({placeholders})
            AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
            AND it.typeName NOT IN ('attachment', 'note')
        """,
            collection_ids,
        )

        items = []
        for row in cursor.fetchall():
            item = LocalZoteroItem(
                item_id=row["itemID"], key=row["key"], item_type=row["typeName"]
            )
            self._populate_item_data(item)
            item.creators = self._get_item_creators(item.item_id)
            item.tags = self._get_item_tags(item.item_id)
            item.attachments = self._get_item_attachments(item.item_id)
            item.collections = self._get_item_collections(item.item_id)
            item.collection_paths = self._get_item_collection_paths(item.item_id)
            items.append(item)

        return items

    def _populate_item_data(self, item: LocalZoteroItem):
        """Populate item with field data."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT f.fieldName, idv.value
            FROM itemData id
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            JOIN fields f ON id.fieldID = f.fieldID
            WHERE id.itemID = ?
        """,
            (item.item_id,),
        )

        for row in cursor.fetchall():
            field_name = row["fieldName"]
            value = row["value"]

            if field_name == "title":
                item.title = value
            elif field_name == "date":
                item.date = value
            elif field_name == "abstractNote":
                item.abstract = value
            elif field_name == "DOI":
                item.doi = value
            elif field_name == "url":
                item.url = value

    def _get_item_creators(self, item_id: int) -> List[Dict[str, str]]:
        """Get creators for an item."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT c.firstName, c.lastName, ct.creatorType
            FROM itemCreators ic
            JOIN creators c ON ic.creatorID = c.creatorID
            JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
            WHERE ic.itemID = ?
            ORDER BY ic.orderIndex
        """,
            (item_id,),
        )

        return [
            {
                "firstName": row["firstName"] or "",
                "lastName": row["lastName"] or "",
                "creatorType": row["creatorType"],
            }
            for row in cursor.fetchall()
        ]

    def _get_item_tags(self, item_id: int) -> List[str]:
        """Get tags for an item."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT t.name
            FROM itemTags it
            JOIN tags t ON it.tagID = t.tagID
            WHERE it.itemID = ?
        """,
            (item_id,),
        )

        return [row["name"] for row in cursor.fetchall()]

    def _get_item_attachments(self, item_id: int) -> List[Dict[str, Any]]:
        """Get attachments for an item."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT i.itemID, i.key, ia.path, ia.contentType
            FROM itemAttachments ia
            JOIN items i ON ia.itemID = i.itemID
            WHERE ia.parentItemID = ?
            AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        """,
            (item_id,),
        )

        attachments = []
        for row in cursor.fetchall():
            path = row["path"]
            # Zotero stores paths with 'storage:' prefix for local files
            if path and path.startswith("storage:"):
                path = path[8:]  # Remove 'storage:' prefix

            attachments.append(
                {
                    "item_id": row["itemID"],
                    "key": row["key"],
                    "path": path,
                    "content_type": row["contentType"],
                }
            )

        return attachments

    def get_all_attachments(self) -> List[Dict[str, Any]]:
        """Get all attachments in the library."""
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT i.itemID, i.key, ia.path, ia.contentType, ia.parentItemID
            FROM itemAttachments ia
            JOIN items i ON ia.itemID = i.itemID
            WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
            AND ia.path IS NOT NULL
        """
        )

        attachments = []
        for row in cursor.fetchall():
            path = row["path"]
            if path and path.startswith("storage:"):
                path = path[8:]

            attachments.append(
                {
                    "item_id": row["itemID"],
                    "key": row["key"],
                    "path": path,
                    "content_type": row["contentType"],
                    "parent_item_id": row["parentItemID"],
                }
            )

        return attachments

    def get_attachment_path(self, attachment_key: str) -> Optional[Path]:
        """
        Get the full file path for an attachment.

        Args:
            attachment_key: The attachment's key

        Returns:
            Full path to the file, or None if not found
        """
        storage_dir = self.original_db_path.parent / "storage"
        attachment_dir = storage_dir / attachment_key

        if attachment_dir.exists():
            # Find the actual file in the attachment directory
            files = list(attachment_dir.iterdir())
            if files:
                return files[0]  # Usually there's one file per attachment

        return None

    def search(self, query: str) -> List[LocalZoteroItem]:
        """
        Search items by title.

        Args:
            query: Search string

        Returns:
            List of matching items
        """
        conn = self._get_connection()
        cursor = conn.execute(
            """
            SELECT DISTINCT i.itemID, i.key, it.typeName
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            JOIN itemData id ON i.itemID = id.itemID
            JOIN itemDataValues idv ON id.valueID = idv.valueID
            JOIN fields f ON id.fieldID = f.fieldID
            WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
            AND it.typeName NOT IN ('attachment', 'note')
            AND f.fieldName = 'title'
            AND idv.value LIKE ?
        """,
            (f"%{query}%",),
        )

        items = []
        for row in cursor.fetchall():
            item = LocalZoteroItem(
                item_id=row["itemID"], key=row["key"], item_type=row["typeName"]
            )
            self._populate_item_data(item)
            item.creators = self._get_item_creators(item.item_id)
            item.tags = self._get_item_tags(item.item_id)
            item.collections = self._get_item_collections(item.item_id)
            item.collection_paths = self._get_item_collection_paths(item.item_id)
            items.append(item)

        return items


def print_collection_tree(collections: List[ZoteroCollection], indent: int = 0):
    """Helper function to print collection tree."""
    for collection in collections:
        print("  " * indent + f"📁 {collection.name}")
        if collection.children:
            print_collection_tree(collection.children, indent + 1)


def main():
    """Example usage of local database reader."""
    print("=" * 60)
    print("ZOTERO LOCAL DATABASE READER")
    print("=" * 60)

    try:
        with ZoteroLocalDB() as db:
            print(f"\nDatabase: {db.original_db_path}")
            print(f"Total items: {db.get_item_count()}")

            # Get collections tree
            print("\n" + "-" * 40)
            print("COLLECTION HIERARCHY:")
            print("-" * 40)
            collections_tree = db.get_collections_tree()
            print_collection_tree(collections_tree)

            # Get collections with full paths
            collections = db.get_collections()
            print(f"\nTotal collections: {len(collections)}")

            # Get sample items with collection info
            items = db.get_all_items()
            print(f"\nLibrary items: {len(items)}")

            print("\nSample items with collections (first 5):")
            for item in items[:5]:
                title = f"{item.title[:50]}..." if len(item.title) > 50 else item.title
                print(f"  • {title}")
                print(
                    f"    Type: {item.item_type}, Attachments: {len(item.attachments)}"
                )
                if item.collection_paths:
                    print(f"    Collections: {', '.join(item.collection_paths)}")
                else:
                    print("    Collections: (none)")

            # Get attachments
            attachments = db.get_all_attachments()
            print(f"\nTotal attachments: {len(attachments)}")

    except FileNotFoundError as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
