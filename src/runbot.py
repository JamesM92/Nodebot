#!/usr/bin/env python3

"""
Simple entrypoint for NodeBot.
This is a minimal script to start the NodeBot system.
"""

import sys
import os

# Add src directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nodebot import NodeBot

if __name__ == "__main__":
    try:
        bot = NodeBot()
    except KeyboardInterrupt:
        print("?? NodeBot shutdown requested")
    except Exception as e:
        print(f"?? NodeBot error: {e}")
        sys.exit(1)