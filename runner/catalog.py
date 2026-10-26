#!/usr/bin/env python3
"""
Splice INSTRUMENT Catalog Resolver & Cache Manager
Fetches and hydrates authentic library, preset, artwork, and audio demo data
from instrument-api.splice.com, merged with local Spitfire properties.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error

CACHE_DIR = os.path.expanduser("~/.cache/splice-instruments")
CACHE_FILE = os.path.join(CACHE_DIR, "catalog.json")
PROPS_FILE = os.path.expanduser("~/.local/share/splice-instruments/drive_c/users/navigator/AppData/Roaming/com.splice.INSTRUMENT/INSTRUMENT-Hurricane.properties")

HEADERS = {
    "X-Client-Id": "WBRfIb1d1fvJfHap7jKeV2PCwkikRJrJ",
    "X-INSTRUMENT-Client-Version": "2.4.18",
    "User-Agent": "INSTRUMENT/2.4.18",
    "Accept": "application/json"
}

def get_installed_state():
    """Returns (installed_libraries_set, installed_presets_set)."""
    installed_libraries = set()
    installed_presets = set()
    if os.path.isfile(PROPS_FILE):
        try:
            with open(PROPS_FILE, "r", encoding="utf-8") as f:
                props = json.load(f)
                for lib in props.get("libraries", []):
                    cid = lib.get("contentId")
                    if cid:
                        installed_libraries.add(cid)
                    for p in lib.get("presets", []):
                        pid = p.get("contentId")
                        if pid:
                            installed_presets.add(pid)
        except Exception as e:
            sys.stderr.write(f"[Catalog] Notice reading properties: {e}\n")
    return installed_libraries, installed_presets

def fetch_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

def sync_catalog(max_libraries=50):
    """Fetches libraries and asset metadata from Splice Cloud."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    sys.stderr.write(f"[Catalog] Querying Splice API for libraries...\n")

    installed_libs, installed_presets = get_installed_state()
    items = []
    seen_cids = set()

    # 0. Always include locally installed libraries first
    if installed_libs:
        try:
            ids_str = ",".join(installed_libs)
            inst_res = fetch_json(f"https://instrument-api.splice.com/api/v2/content/content-items?ids={ids_str}")
            for it in inst_res.get("items", []):
                cid = it.get("content_id")
                if cid and cid not in seen_cids:
                    seen_cids.add(cid)
                    items.append(it)
            sys.stderr.write(f"[Catalog] Loaded {len(items)} locally installed libraries from properties.\n")
        except Exception as e:
            sys.stderr.write(f"[Catalog] Error loading installed libraries: {e}\n")

    # 1. Fetch free libraries
    try:
        free_res = fetch_json(f"https://instrument-api.splice.com/api/v2/content/content-items/search?type=library&filter%5Bfree%5D=1&per_page=50&page=1")
        for it in free_res.get("items", []):
            cid = it.get("content_id")
            if cid and cid not in seen_cids:
                seen_cids.add(cid)
                items.append(it)
    except Exception as e:
        sys.stderr.write(f"[Catalog] Error querying free libraries: {e}\n")

    # 2. Fetch general catalog libraries
    try:
        search_res = fetch_json(f"https://instrument-api.splice.com/api/v2/content/content-items/search?type=library&per_page=30&page=1")
        for it in search_res.get("items", []):
            cid = it.get("content_id")
            if cid and cid not in seen_cids:
                seen_cids.add(cid)
                items.append(it)
    except Exception as e:
        sys.stderr.write(f"[Catalog] Error querying general search: {e}\n")

    if not items:
        sys.stderr.write("[Catalog] No library items returned.\n")
        return []

    # 3. Collect sound IDs from all libraries
    sound_ids_all = []
    for it in items:
        for sid in it.get("sound_ids", []):
            if sid not in sound_ids_all:
                sound_ids_all.append(sid)

    # 4. Resolve sound items first
    sound_items = {}
    for i in range(0, len(sound_ids_all), 50):
        chunk = sound_ids_all[i:i+50]
        try:
            res = fetch_json("https://instrument-api.splice.com/api/v2/content/content-items?ids=" + ",".join(chunk))
            for it in res.get("items", []):
                sound_items[it["content_id"]] = it
        except Exception as e:
            sys.stderr.write(f"[Catalog] Error resolving sounds chunk: {e}\n")

    # 5. Collect all media IDs from both libraries and resolved sounds
    item_ids = []
    for it in items:
        for k in ["square_instrument_image_id", "image_id", "audio_demo_id"]:
            val = it.get(k)
            if val and val not in item_ids:
                item_ids.append(val)

    for s_obj in sound_items.values():
        val = s_obj.get("audio_demo_id")
        if val and val not in item_ids:
            item_ids.append(val)

    # 6. Resolve media items
    media_items = {}
    for i in range(0, len(item_ids), 50):
        chunk = item_ids[i:i+50]
        try:
            res = fetch_json("https://instrument-api.splice.com/api/v2/content/content-items?ids=" + ",".join(chunk))
            for it in res.get("items", []):
                media_items[it["content_id"]] = it
        except Exception as e:
            sys.stderr.write(f"[Catalog] Error resolving media chunk: {e}\n")

    # 7. Resolve direct assets (images / audio demo files)
    asset_ids = []
    for it in media_items.values():
        aid = it.get("image_id") or it.get("source_id")
        if aid and aid not in asset_ids:
            asset_ids.append(aid)

    asset_urls = {}
    for i in range(0, len(asset_ids), 50):
        chunk = asset_ids[i:i+50]
        try:
            res = fetch_json("https://instrument-api.splice.com/api/v2/content/content-assets?ids=" + ",".join(chunk))
            for a in res.get("assets", []):
                aid = a.get("asset_id")
                url = a.get("file", {}).get("url", "")
                if url.startswith("//"):
                    url = "https:" + url
                asset_urls[aid] = url
        except Exception as e:
            sys.stderr.write(f"[Catalog] Error resolving assets chunk: {e}\n")

    DEFAULT_DEMO_URL = "https://assets.ctfassets.net/g7eq1prqo1vw/1rO6VIIIpePmcdhZpnSlKv/b5e4168b16da5d985422614de28e9468/LW_ambient_guitars_demo_v2limit_rx.mp3"

    # 8. Build library models
    catalog = []
    for it in items:
        cid = it.get("content_id")
        img_item = media_items.get(it.get("square_instrument_image_id") or it.get("image_id"), {})
        img_url = asset_urls.get(img_item.get("image_id") or img_item.get("source_id"), "")

        demo_item = media_items.get(it.get("audio_demo_id"), {})
        demo_url = asset_urls.get(demo_item.get("source_id"), "") or DEFAULT_DEMO_URL

        sounds = []
        for sid in it.get("sound_ids", []):
            s_obj = sound_items.get(sid, {})
            s_demo_item = media_items.get(s_obj.get("audio_demo_id"), {})
            s_demo_url = asset_urls.get(s_demo_item.get("source_id"), "") or demo_url or DEFAULT_DEMO_URL
            sounds.append({
                "contentId": sid,
                "name": s_obj.get("name", "Preset"),
                "uniqueName": s_obj.get("internal_name") or s_obj.get("name", "Preset"),
                "audioDemoUrl": s_demo_url,
                "installState": 3 if sid in installed_presets else 4,
                "isAccessible": True
            })

        catalog.append({
            "familyName": it.get("name"),
            "contentId": cid,
            "name": it.get("name"),
            "displayName": it.get("name"),
            "isAccessible": True,
            "summary": it.get("summary", ""),
            "imageURL": img_url,
            "slabImageURL": img_url,
            "logoURL": "",
            "backgroundImageURL": img_url,
            "numberOfSounds": len(sounds) or it.get("number_of_sounds", 0),
            "size": 100,
            "releaseDate": it.get("release_date", "2024-01-01"),
            "isFree": it.get("free", False),
            "isNew": it.get("is_new", False),
            "primaryColourHex": it.get("primary_color", {}).get("hex", "#444444") if isinstance(it.get("primary_color"), dict) else "#444444",
            "secondaryColourHex": it.get("secondary_color", {}).get("hex", "#666666") if isinstance(it.get("secondary_color"), dict) else "#666666",
            "brand": it.get("brand", "Spitfire Audio"),
            "audioDemos": [{"contentId": "1", "url": demo_url}],
            "sounds": sounds,
            "labels": []
        })

    # Save to disk cache
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2)
        sys.stderr.write(f"[Catalog] Saved {len(catalog)} libraries to cache {CACHE_FILE}\n")
    except Exception as e:
        sys.stderr.write(f"[Catalog] Warning: Failed to write cache: {e}\n")

    return catalog

    return catalog

def get_catalog(force_refresh=False):
    """Returns hydrated catalog list with up-to-date install states."""
    catalog = None
    if not force_refresh and os.path.isfile(CACHE_FILE):
        try:
            mtime = os.path.getmtime(CACHE_FILE)
            if time.time() - mtime < 86400 * 7: # Cache valid for 7 days
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    catalog = json.load(f)
        except Exception as e:
            sys.stderr.write(f"[Catalog] Cache read error: {e}\n")

    if not catalog:
        catalog = sync_catalog()

    if not catalog:
        return []

    # Update installState dynamically from live properties
    installed_libs, installed_presets = get_installed_state()
    for lib in catalog:
        for s in lib.get("sounds", []):
            sid = s.get("contentId")
            s["installState"] = 3 if sid in installed_presets else 4

    return catalog

if __name__ == "__main__":
    cat = get_catalog(force_refresh=True)
    print(f"Loaded {len(cat)} libraries.")
    for l in cat[:5]:
        print(f" - {l['displayName']} ({l['brand']}): {len(l['sounds'])} presets")
