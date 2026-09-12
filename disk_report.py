import os
from pathlib import Path
import sys

def get_dir_size(path: Path) -> int:
    total = 0
    try:
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
    except Exception:
        pass
    return total

def generate_disk_report(root: Path):
    archives_dir = root / "Archives"
    if not archives_dir.exists():
        print("Archives directory does not exist.")
        return

    run_dirs = [d for d in archives_dir.iterdir() if d.is_dir() and d.name.startswith("Run_")]
    count = len(run_dirs)
    
    total_bytes = get_dir_size(archives_dir)
    total_mb = total_bytes / (1024 * 1024)
    
    print("=== Archives Disk Report ===")
    print(f"Total Archived Runs: {count}")
    print(f"Total Size: {total_mb:.2f} MB ({total_bytes} bytes)")
    
if __name__ == "__main__":
    generate_disk_report(Path.cwd())

