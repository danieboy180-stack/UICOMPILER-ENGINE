import sys
import subprocess
from pathlib import Path

def main():
    root = Path.cwd()
    print("=== CI GATE: Verification Integrity ===")
    
    # 1. Run the static linter
    print("\n[1/3] Running static test linter (verification/lint_tests.py)...")
    res_lint = subprocess.run([sys.executable, "verification/lint_tests.py"])
    if res_lint.returncode != 0:
        print("\nCI REJECTED: Linter failed. You have illegal test patterns.")
        sys.exit(1)
        
    # 2. Run all tests via pytest
    print("\n[2/3] Running test suite via pytest...")
    res_pytest = subprocess.run([sys.executable, "-m", "pytest", "-s"])
    if res_pytest.returncode != 0:
        print("\nCI REJECTED: Tests failed. All tests must pass (and be verifiable).")
        sys.exit(1)
        
    # 3. Check Verification Ledger
    print("\n[3/3] Checking Verification Ledger...")
    ledger_path = root / "Logs" / "Verification_Ledger.json"
    if not ledger_path.exists():
        print("\nCI REJECTED: Verification_Ledger.json not found. No verification receipts recorded.")
        sys.exit(1)
        
    import json
    with open(ledger_path, 'r', encoding='utf-8') as f:
        lines = f.read().strip().splitlines()
        
    if not lines:
        print("\nCI REJECTED: Verification Ledger is empty.")
        sys.exit(1)
        
    print(f"Verified {len(lines)} receipts in the ledger.")
    print("\nCI PASSED: Stage verification criteria fully met.")
    sys.exit(0)

if __name__ == "__main__":
    main()

