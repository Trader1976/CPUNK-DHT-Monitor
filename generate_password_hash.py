#!/usr/bin/env python3
"""
Generate a SHA3-512 password hash for DHT_MONITOR_PASS_HASH.

Usage:
  python3 generate_monitor_hash.py

It will:
  - Prompt for password (hidden)
  - Print SHA3-512 hex digest
"""

import hashlib
import getpass

def main():
    pwd = getpass.getpass("Enter password to hash (SHA3-512): ")
    pwd2 = getpass.getpass("Repeat password: ")

    if pwd != pwd2:
        print("Error: passwords do not match.")
        return

    h = hashlib.sha3_512(pwd.encode("utf-8")).hexdigest()
    print("\nSHA3-512 hex (set this as DHT_MONITOR_PASS_HASH):")
    print(h)

if __name__ == "__main__":
    main()
