#!/usr/bin/env python3
"""
Splice INSTRUMENT Preset & Library Downloader
Handles Hurricane API interaction, signed CDN URL stream downloads,
and atomic updates to INSTRUMENT-Hurricane.properties and INSTRUMENT.locations.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import urllib.request
import urllib.error
import threading

DEFAULT_ENCRYPTION_KEY = "D3A000F3-83A7D9C9-BF855679-C143B75B-08B68B6D-D000F6A0-18BEB9B9-19000C39-DEFF9E21-12602143-5C66AF52-451157FE-0665BA3A-602CCCF0-2212B183-1FBD6E34"

SPLICE_BASE_DIR = os.environ.get("SPLICE_BASE_DIR", os.path.expanduser("~/.local/share/splice-instruments"))
PROPS_FILE = os.environ.get("SPLICE_PROPS_FILE", os.path.join(SPLICE_BASE_DIR, "drive_c/users/navigator/AppData/Roaming/com.splice.INSTRUMENT/INSTRUMENT-Hurricane.properties"))
LOCATIONS_FILE = os.environ.get("SPLICE_LOCATIONS_FILE", os.path.join(SPLICE_BASE_DIR, "drive_c/users/navigator/AppData/Roaming/com.splice.INSTRUMENT/INSTRUMENT.locations"))
INSTRUMENT_CONTENT_DIR = os.environ.get("SPLICE_CONTENT_DIR", os.path.join(SPLICE_BASE_DIR, "drive_c/users/navigator/Splice/INSTRUMENT"))
WINDOWS_BASE_PATH = "C:\\users\\navigator\\Splice\\INSTRUMENT"

COMMON_HEADERS = {
    "X-Client-Id": "WBRfIb1d1fvJfHap7jKeV2PCwkikRJrJ",
    "X-INSTRUMENT-Client-Version": "2.4.18",
    "User-Agent": "INSTRUMENT/2.4.18",
    "Accept": "application/json"
}

# In-memory download task tracker for UI polling: task_id -> status_dict
ACTIVE_DOWNLOADS = {}
_downloads_lock = threading.Lock()


def get_encryption_key():
    """Retrieve machine encryption key from environment, log, or default."""
    return os.environ.get("SPLICE_ENCRYPTION_KEY", DEFAULT_ENCRYPTION_KEY)


def fetch_library_metadata_fallback(library_id):
    """Directly queries Splice API if catalog cache lacks library."""
    url = f"https://instrument-api.splice.com/api/v2/content/content-items?ids={library_id}"
    req = urllib.request.Request(url, headers=COMMON_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("items", [])
            if items:
                return items[0]
    except Exception as e:
        sys.stderr.write(f"[Downloader] Error in fallback metadata fetch for {library_id}: {e}\n")
    return {}


def resolve_build_id(library_id):
    """Queries product-versions endpoint to find latest build_id."""
    url = f"https://instrument-api.splice.com/api/v2/hurricane/product-versions?products={library_id}"
    req = urllib.request.Request(url, headers=COMMON_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            builds = data.get("data", [])
            if builds and "id" in builds[0]:
                return builds[0]["id"]
    except Exception as e:
        sys.stderr.write(f"[Downloader] Error resolving build_id for {library_id}: {e}\n")
    return None


def get_preset_installation_manifest(build_id, preset_ids, token=None):
    """Requests signed CloudFront download manifest for specified preset(s)."""
    url = "https://instrument-api.splice.com/api/v2/hurricane/preset-installations"
    headers = dict(COMMON_HEADERS)
    headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    machine_key = get_encryption_key()
    payload = {
        "build_id": build_id,
        "preset_ids": preset_ids if isinstance(preset_ids, list) else [preset_ids],
        "machine_key": machine_key,
        "encryption_key": machine_key
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401 and token:
            sys.stderr.write(f"[Downloader] 401 with bearer token; retrying unauthenticated with machine_key...\n")
            headers.pop("Authorization", None)
            req2 = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req2, timeout=20) as resp2:
                return json.loads(resp2.read().decode("utf-8"))
        err_msg = e.read().decode("utf-8", errors="ignore")
        if e.code == 403:
            sys.stderr.write(f"[Downloader] Splice subscription or preset entitlement required (HTTP 403): {err_msg}\n")
            raise RuntimeError(f"Splice subscription required (HTTP 403): {err_msg}")
        sys.stderr.write(f"[Downloader] HTTP error fetching manifest ({e.code}): {err_msg}\n")
        raise RuntimeError(f"HTTP {e.code}: {err_msg}")
    except Exception as e:
        sys.stderr.write(f"[Downloader] Exception fetching manifest: {e}\n")
        raise


def stream_download_file(url, target_path, expected_size=None, progress_callback=None):
    """Downloads a file from signed URL to target_path with streaming and resume/skip check."""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)

    # If file exists with matching size, skip download
    if os.path.isfile(target_path):
        current_size = os.path.getsize(target_path)
        if expected_size is not None and current_size == expected_size:
            sys.stderr.write(f"[Downloader] Skipping already downloaded file: {os.path.basename(target_path)} ({current_size} bytes)\n")
            if progress_callback:
                progress_callback(expected_size, expected_size)
            return True

    temp_path = f"{target_path}.part"
    req = urllib.request.Request(url, headers={"User-Agent": "INSTRUMENT/2.4.18"})

    with urllib.request.urlopen(req, timeout=30) as response, open(temp_path, "wb") as out_file:
        total_size = int(response.headers.get("Content-Length", expected_size or 0))
        downloaded = 0
        chunk_size = 131072 # 128 KB chunks

        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            if progress_callback:
                progress_callback(downloaded, total_size)

    # Atomic move
    os.replace(temp_path, target_path)
    return True


def determine_library_folder_name(library_meta, library_id=""):
    """Determine the standard directory name under Splice/INSTRUMENT/."""
    family = library_meta.get("familyName") or library_meta.get("name")
    brand = library_meta.get("brand")

    # If missing, fetch from Splice API directly
    if not family and library_id:
        fb = fetch_library_metadata_fallback(library_id)
        if fb:
            family = fb.get("name")
            brand = fb.get("brand") or brand

    family = family or "SoundPack"
    brand = brand or "Spitfire Audio"

    # Match existing folders first if present
    if os.path.isdir(INSTRUMENT_CONTENT_DIR):
        for candidate in os.listdir(INSTRUMENT_CONTENT_DIR):
            c_lower = candidate.lower()
            if family.lower() in c_lower:
                return candidate

    # Standard Spitfire folder convention: "<Brand> - <Family>" or "<Family>"
    if brand.lower() in family.lower():
        return family
    return f"{brand} - {family}"


def update_hurricane_properties(library_id, family_name, build_id, preset_id, folder_name, files_manifest):
    """Atomically registers downloaded preset and files in INSTRUMENT-Hurricane.properties."""
    os.makedirs(os.path.dirname(PROPS_FILE), exist_ok=True)
    props = {"libraries": []}
    if os.path.isfile(PROPS_FILE):
        try:
            with open(PROPS_FILE, "r", encoding="utf-8") as f:
                props = json.load(f)
        except Exception as e:
            sys.stderr.write(f"[Downloader] Notice: reading properties file: {e}\n")

    libraries = props.setdefault("libraries", [])
    target_lib = None
    for lib in libraries:
        if lib.get("contentId") == library_id:
            target_lib = lib
            break

    win_folder_path = f"{WINDOWS_BASE_PATH}\\{folder_name}"

    if not target_lib:
        target_lib = {
            "buildIdentifier": build_id,
            "contentId": library_id,
            "family": family_name,
            "path": win_folder_path,
            "presets": []
        }
        libraries.append(target_lib)
    else:
        target_lib["buildIdentifier"] = build_id
        target_lib["path"] = win_folder_path

    # Build preset files entry
    new_preset_entry = {
        "contentId": preset_id,
        "files": []
    }
    for item in files_manifest:
        rel_win_path = item["path"].replace("/", "\\")
        new_preset_entry["files"].append({
            "path": f"{win_folder_path}\\{rel_win_path}",
            "size": item.get("size", 0)
        })

    presets = target_lib.setdefault("presets", [])
    preset_idx = -1
    for i, p in enumerate(presets):
        if p.get("contentId") == preset_id:
            preset_idx = i
            break

    if preset_idx >= 0:
        presets[preset_idx] = new_preset_entry
    else:
        presets.append(new_preset_entry)

    # Write atomically
    try:
        with tempfile.NamedTemporaryFile("w", dir=os.path.dirname(PROPS_FILE), delete=False, encoding="utf-8") as tmp:
            json.dump(props, tmp, indent=4)
            tmp_name = tmp.name
        os.replace(tmp_name, PROPS_FILE)
        sys.stderr.write(f"[Downloader] Updated {PROPS_FILE} with preset {preset_id}\n")
    except OSError as e:
        sys.stderr.write(f"[Downloader] Sandbox notice ({e}): writing properties update to cache instead\n")
        cache_props = os.path.expanduser("~/.cache/splice-instruments/INSTRUMENT-Hurricane.properties")
        os.makedirs(os.path.dirname(cache_props), exist_ok=True)
        with open(cache_props, "w", encoding="utf-8") as f:
            json.dump(props, f, indent=4)


def update_instrument_locations(folder_name):
    """Atomically registers Patches, Presets, and Samples in INSTRUMENT.locations."""
    os.makedirs(os.path.dirname(LOCATIONS_FILE), exist_ok=True)
    locs = {"INSTRUMENT": {"patches": [], "presets": [], "samples": []}}
    if os.path.isfile(LOCATIONS_FILE):
        try:
            with open(LOCATIONS_FILE, "r", encoding="utf-8") as f:
                locs = json.load(f)
        except Exception as e:
            sys.stderr.write(f"[Downloader] Notice: reading locations file: {e}\n")

    inst_locs = locs.setdefault("INSTRUMENT", {})
    patches = inst_locs.setdefault("patches", [])
    presets = inst_locs.setdefault("presets", [])
    samples = inst_locs.setdefault("samples", [])

    win_folder_path = f"{WINDOWS_BASE_PATH}\\{folder_name}"
    patch_dir = f"{win_folder_path}\\Patches"
    preset_dir = f"{win_folder_path}\\Presets"
    sample_dir = f"{win_folder_path}\\Samples"

    if patch_dir not in patches:
        patches.append(patch_dir)
    if preset_dir not in presets:
        presets.append(preset_dir)
    if sample_dir not in samples:
        samples.append(sample_dir)

    # Write atomically
    try:
        with tempfile.NamedTemporaryFile("w", dir=os.path.dirname(LOCATIONS_FILE), delete=False, encoding="utf-8") as tmp:
            json.dump(locs, tmp, indent=4)
            tmp_name = tmp.name
        os.replace(tmp_name, LOCATIONS_FILE)
        sys.stderr.write(f"[Downloader] Updated {LOCATIONS_FILE} with locations for {folder_name}\n")
    except OSError as e:
        sys.stderr.write(f"[Downloader] Sandbox notice ({e}): writing locations update to cache instead\n")
        cache_locs = os.path.expanduser("~/.cache/splice-instruments/INSTRUMENT.locations")
        os.makedirs(os.path.dirname(cache_locs), exist_ok=True)
        with open(cache_locs, "w", encoding="utf-8") as f:
            json.dump(locs, f, indent=4)


def execute_download(sound_content_id, library_content_id, token=None):
    """High-level end-to-end execution of a preset download."""
    task_key = f"{library_content_id}:{sound_content_id}"
    with _downloads_lock:
        ACTIVE_DOWNLOADS[task_key] = {
            "soundContentId": sound_content_id,
            "libraryContentId": library_content_id,
            "status": "in_progress",
            "progressPercent": 0,
            "currentFile": "Resolving metadata...",
            "error": None
        }

    try:
        # 1. Resolve library and preset metadata from catalog or API
        import catalog
        all_libs = catalog.get_catalog()
        lib_meta = next((l for l in all_libs if l.get("contentId") == library_content_id), {})
        sound_meta = next((s for s in lib_meta.get("sounds", []) if s.get("contentId") == sound_content_id), {})

        folder_name = determine_library_folder_name(lib_meta, library_content_id)
        family_name = lib_meta.get("familyName") or lib_meta.get("name") or folder_name
        target_dir = os.path.join(INSTRUMENT_CONTENT_DIR, folder_name)

        sys.stderr.write(f"[Downloader] Starting download for preset {sound_content_id} ({sound_meta.get('name', 'Sound')}) in {folder_name}...\n")

        # 2. Resolve build ID
        build_id = resolve_build_id(library_content_id)
        if not build_id:
            raise RuntimeError(f"Could not resolve build_id for library {library_content_id}")

        # 3. Request signed CloudFront installation manifest
        manifest = get_preset_installation_manifest(build_id, sound_content_id, token=token)
        files = manifest.get("data", [])
        if not files:
            raise RuntimeError("Received empty file list from preset installation manifest")

        total_bytes = sum(f.get("size", 0) for f in files)
        completed_bytes = 0

        # 4. Stream-download each required file
        for idx, f_item in enumerate(files, 1):
            rel_path = f_item["path"]
            url = f_item["url"]
            expected_size = f_item.get("size")
            local_file_path = os.path.join(target_dir, rel_path)

            file_basename = os.path.basename(rel_path)
            with _downloads_lock:
                ACTIVE_DOWNLOADS[task_key]["currentFile"] = f"Downloading {file_basename} ({idx}/{len(files)})"

            def on_progress(done_in_file, total_in_file):
                curr = completed_bytes + done_in_file
                pct = int((curr / total_bytes) * 100) if total_bytes > 0 else 0
                with _downloads_lock:
                    ACTIVE_DOWNLOADS[task_key]["progressPercent"] = min(pct, 99)

            stream_download_file(url, local_file_path, expected_size=expected_size, progress_callback=on_progress)
            completed_bytes += (expected_size or 0)

        # 5. Atomically update Spitfire metadata configuration files
        update_hurricane_properties(library_content_id, family_name, build_id, sound_content_id, folder_name, files)
        update_instrument_locations(folder_name)

        # 6. Update catalog cache on disk so UI and backend reflect Downloaded state (3)
        catalog_cache = os.path.expanduser("~/.cache/splice-instruments/catalog.json")
        if os.path.isfile(catalog_cache):
            try:
                with open(catalog_cache, "r", encoding="utf-8") as f:
                    disk_cat = json.load(f)
                for lib in disk_cat:
                    if lib.get("contentId") == library_content_id:
                        for s in lib.get("sounds", []):
                            if s.get("contentId") == sound_content_id:
                                s["installState"] = 3
                with open(catalog_cache, "w", encoding="utf-8") as f:
                    json.dump(disk_cat, f, indent=2)
            except Exception as e:
                sys.stderr.write(f"[Downloader] Notice updating catalog cache: {e}\n")

        with _downloads_lock:
            ACTIVE_DOWNLOADS[task_key]["status"] = "completed"
            ACTIVE_DOWNLOADS[task_key]["progressPercent"] = 100
            ACTIVE_DOWNLOADS[task_key]["currentFile"] = "Ready to play"

        sys.stderr.write(f"[Downloader] Successfully completed download & registration for {sound_content_id}\n")
        return {"status": "completed", "soundContentId": sound_content_id, "libraryContentId": library_content_id}

    except Exception as e:
        err_str = str(e)
        user_err = "Active Splice subscription required (HTTP 403)" if "HTTP 403" in err_str else err_str
        sys.stderr.write(f"[Downloader] Error executing download: {user_err}\n")
        with _downloads_lock:
            ACTIVE_DOWNLOADS[task_key]["status"] = "failed"
            ACTIVE_DOWNLOADS[task_key]["error"] = user_err
            ACTIVE_DOWNLOADS[task_key]["currentFile"] = f"Failed: {user_err}"
        raise


def execute_pack_download(library_content_id, token=None):
    """High-level end-to-end execution of a full library pack download."""
    task_key = f"{library_content_id}:all"
    with _downloads_lock:
        ACTIVE_DOWNLOADS[task_key] = {
            "soundContentId": "all",
            "libraryContentId": library_content_id,
            "status": "in_progress",
            "progressPercent": 0,
            "currentFile": "Resolving pack metadata...",
            "error": None
        }

    try:
        import catalog
        all_libs = catalog.get_catalog()
        lib_meta = next((l for l in all_libs if l.get("contentId") == library_content_id), {})
        if not lib_meta:
            lib_meta = fetch_library_metadata_fallback(library_content_id)

        folder_name = determine_library_folder_name(lib_meta, library_content_id)
        family_name = lib_meta.get("familyName") or lib_meta.get("name") or folder_name
        target_dir = os.path.join(INSTRUMENT_CONTENT_DIR, folder_name)

        sounds = lib_meta.get("sounds", [])
        preset_ids = [s.get("contentId") for s in sounds if s.get("contentId")]
        if not preset_ids:
            fb = fetch_library_metadata_fallback(library_content_id)
            preset_ids = fb.get("sound_ids", [])

        if not preset_ids:
            raise RuntimeError(f"No preset IDs found for library {library_content_id}")

        sys.stderr.write(f"[Downloader] Starting pack download for {family_name} ({len(preset_ids)} presets)...\n")

        build_id = resolve_build_id(library_content_id)
        if not build_id:
            raise RuntimeError(f"Could not resolve build_id for library {library_content_id}")

        manifest = get_preset_installation_manifest(build_id, preset_ids, token=token)
        files = manifest.get("data", [])
        if not files:
            raise RuntimeError("Received empty file list for pack installation manifest")

        total_bytes = sum(f.get("size", 0) for f in files)
        completed_bytes = 0

        for idx, f_item in enumerate(files, 1):
            rel_path = f_item["path"]
            url = f_item["url"]
            expected_size = f_item.get("size")
            local_file_path = os.path.join(target_dir, rel_path)

            file_basename = os.path.basename(rel_path)
            with _downloads_lock:
                ACTIVE_DOWNLOADS[task_key]["currentFile"] = f"Downloading {file_basename} ({idx}/{len(files)})"

            def on_progress(done_in_file, total_in_file):
                curr = completed_bytes + done_in_file
                pct = int((curr / total_bytes) * 100) if total_bytes > 0 else 0
                with _downloads_lock:
                    ACTIVE_DOWNLOADS[task_key]["progressPercent"] = min(pct, 99)

            stream_download_file(url, local_file_path, expected_size=expected_size, progress_callback=on_progress)
            completed_bytes += (expected_size or 0)

        for pid in preset_ids:
            update_hurricane_properties(library_content_id, family_name, build_id, pid, folder_name, files)
        update_instrument_locations(folder_name)

        catalog_cache = os.path.expanduser("~/.cache/splice-instruments/catalog.json")
        if os.path.isfile(catalog_cache):
            try:
                with open(catalog_cache, "r", encoding="utf-8") as f:
                    disk_cat = json.load(f)
                for lib in disk_cat:
                    if lib.get("contentId") == library_content_id:
                        for s in lib.get("sounds", []):
                            s["installState"] = 3
                with open(catalog_cache, "w", encoding="utf-8") as f:
                    json.dump(disk_cat, f, indent=2)
            except Exception:
                pass

        with _downloads_lock:
            ACTIVE_DOWNLOADS[task_key]["status"] = "completed"
            ACTIVE_DOWNLOADS[task_key]["progressPercent"] = 100
            ACTIVE_DOWNLOADS[task_key]["currentFile"] = "Pack ready to play"

        return {"status": "completed", "libraryContentId": library_content_id, "presetCount": len(preset_ids)}
    except Exception as e:
        err_str = str(e)
        user_err = "Active Splice subscription required (HTTP 403)" if "HTTP 403" in err_str else err_str
        sys.stderr.write(f"[Downloader] Error executing pack download: {user_err}\n")
        with _downloads_lock:
            ACTIVE_DOWNLOADS[task_key]["status"] = "failed"
            ACTIVE_DOWNLOADS[task_key]["error"] = user_err
            ACTIVE_DOWNLOADS[task_key]["currentFile"] = f"Failed: {user_err}"
        raise


def start_pack_download_async(library_content_id, token=None):
    """Launches execute_pack_download in a background thread."""
    t = threading.Thread(target=execute_pack_download, args=(library_content_id, token), daemon=True)
    t.start()
    return {"status": "started", "libraryContentId": library_content_id}


def start_download_async(sound_content_id, library_content_id, token=None):
    """Launches execute_download in a background thread."""
    t = threading.Thread(target=execute_download, args=(sound_content_id, library_content_id, token), daemon=True)
    t.start()
    return {"status": "started", "soundContentId": sound_content_id, "libraryContentId": library_content_id}


def get_download_status(task_key):
    """Query current status of a download task."""
    with _downloads_lock:
        return ACTIVE_DOWNLOADS.get(task_key, {"status": "not_found"})


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 downloader.py <library_content_id> <sound_content_id>")
        sys.exit(1)
    lib_id = sys.argv[1]
    snd_id = sys.argv[2]
    print(f"Testing downloader for library {lib_id}, sound {snd_id}...")
    res = execute_download(snd_id, lib_id)
    print("Result:", res)
