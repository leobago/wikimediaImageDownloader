#!/usr/bin/env python3

import os
import re
import time
import requests
from urllib.parse import quote
from collections import deque, defaultdict

CATEGORY = "Commons featured desktop backgrounds"  # replace with your category
OUTDIR = "tiles"
EXTENSIONS = re.compile(r"\.(jpg|jpeg|png)$", re.IGNORECASE)
MAX_DEPTH = 10  # How deep to scan for preview
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Referer": "https://commons.wikimedia.org/",
}


def sanitize_filename(name):
    """Remove or replace characters invalid for filenames."""
    return re.sub(r'[<>:"/\\|?*]', '_', name)


def get_category_members(category, cmtype="file", max_retries=5):
    """Get all members of a category (files or subcats)."""
    results = []
    continue_token = ""

    while True:
        for attempt in range(max_retries):
            try:
                time.sleep(0.2)  # Throttle: 5 requests/second max
                resp = requests.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "categorymembers",
                        "cmtitle": f"Category:{category}",
                        "cmtype": cmtype,
                        "cmlimit": 500,
                        "cmcontinue": continue_token,
                        "format": "json",
                    },
                    headers=HEADERS,
                    timeout=30,
                )
                if resp.status_code == 429:
                    wait = 2 ** attempt  # Exponential backoff: 1, 2, 4, 8, 16 seconds
                    print(f"\n  Rate limited. Waiting {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break  # Success, exit retry loop
            except requests.RequestException as e:
                if attempt == max_retries - 1:
                    print(f"\nAPI request failed after {max_retries} attempts: {e}")
                    return results
                time.sleep(2 ** attempt)
        else:
            # All retries exhausted
            return results

        results.extend(data.get("query", {}).get("categorymembers", []))
        continue_token = data.get("continue", {}).get("cmcontinue", "")
        if not continue_token:
            break

    return results


def scan_categories(start_category, max_depth):
    """Scan category tree and return files organized by depth."""
    files_by_depth = defaultdict(list)
    categories_by_depth = defaultdict(int)

    queue = deque([(start_category, 0)])
    processed = set()

    print(f"Scanning category tree (max depth: {max_depth})...\n")

    while queue:
        category, depth = queue.popleft()

        if category in processed or depth > max_depth:
            continue
        processed.add(category)

        categories_by_depth[depth] += 1
        print(f"  Scanning: {category} (depth {depth})", end="\r")

        # Get subcategories
        if depth < max_depth:
            subcats = get_category_members(category, cmtype="subcat")
            for subcat in subcats:
                subcat_name = subcat.get("title", "").removeprefix("Category:")
                queue.append((subcat_name, depth + 1))

        # Get files
        files = get_category_members(category, cmtype="file")
        for f in files:
            title = f.get("title", "")
            if EXTENSIONS.search(title):
                files_by_depth[depth].append((category, title))

    print(" " * 80)  # Clear the line
    return files_by_depth, categories_by_depth


def print_summary(files_by_depth, categories_by_depth):
    """Print summary of files found at each depth."""
    print("\n" + "=" * 60)
    print("SCAN RESULTS")
    print("=" * 60)

    total_files = 0
    cumulative = 0

    print(f"\n{'Depth':<8}{'Categories':<15}{'Images':<15}{'Cumulative':<15}")
    print("-" * 53)

    max_depth = max(list(files_by_depth.keys()) + list(categories_by_depth.keys()), default=0)

    for depth in range(max_depth + 1):
        num_cats = categories_by_depth.get(depth, 0)
        num_files = len(files_by_depth.get(depth, []))
        total_files += num_files
        cumulative += num_files
        print(f"{depth:<8}{num_cats:<15}{num_files:<15}{cumulative:<15}")

    print("-" * 53)
    print(f"{'TOTAL':<8}{sum(categories_by_depth.values()):<15}{total_files:<15}")
    print()

    return total_files


def download_files(files_by_depth, max_download_depth):
    """Download files up to specified depth."""
    os.makedirs(OUTDIR, exist_ok=True)

    downloaded_count = 0
    error_count = 0

    for depth in range(max_download_depth + 1):
        files = files_by_depth.get(depth, [])
        if not files:
            continue

        print(f"\n{'='*60}")
        print(f"Downloading depth {depth} ({len(files)} files)")
        print(f"{'='*60}")

        for category, title in files:
            filename = title.removeprefix("File:")
            encoded = quote(filename, safe="")
            url = f"https://commons.wikimedia.org/wiki/Special:FilePath/{encoded}"

            safe_filename = sanitize_filename(filename)
            outpath = os.path.join(OUTDIR, safe_filename)

            if os.path.exists(outpath):
                print(f"Skipping (exists): {safe_filename}")
                continue

            print(f"Downloading: {safe_filename}")
            for attempt in range(5):
                try:
                    img = requests.get(url, headers=HEADERS, allow_redirects=True, timeout=60)
                    if img.status_code == 429:
                        wait = 2 ** (attempt + 2)  # 4, 8, 16, 32, 64 seconds
                        print(f"  Rate limited. Waiting {wait}s...")
                        time.sleep(wait)
                        continue
                    img.raise_for_status()
                    with open(outpath, "wb") as f:
                        f.write(img.content)
                    downloaded_count += 1
                    print(f"  Saved ({downloaded_count})")
                    break
                except requests.RequestException as e:
                    if attempt == 4:
                        error_count += 1
                        print(f"  Error: {e}")
                    else:
                        time.sleep(2 ** attempt)

            time.sleep(1)  # 1 second between downloads

    return downloaded_count, error_count


# Main execution
if __name__ == "__main__":
    # Phase 1: Scan
    files_by_depth, categories_by_depth = scan_categories(CATEGORY, MAX_DEPTH)
    total = print_summary(files_by_depth, categories_by_depth)

    if total == 0:
        print("No images found.")
        exit(0)

    # Phase 2: Ask user
    print("Enter max depth to download (or 'q' to quit): ", end="")
    choice = input().strip()

    if choice.lower() == 'q':
        print("Aborted.")
        exit(0)

    try:
        download_depth = int(choice)
    except ValueError:
        print("Invalid input. Aborted.")
        exit(1)

    # Phase 3: Download
    downloaded, errors = download_files(files_by_depth, download_depth)
    print(f"\nDone! Downloaded: {downloaded}, Errors: {errors}")
